"""Live mod releases do not depend on rebuilding LLE or replacing contributor work."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lekmod_localization import release_feed as feed
from lekmod_localization.version_history import read_history, between


class ReleaseFeedTests(unittest.TestCase):
    def setUp(self):
        """Keep remote metadata and private caches isolated from real projects."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.releases = {'v35.6': {'release_date': '2026-10-07'},
                         'v35.5': {'release_date': '2026-10-06'},
                         'v35.4': {'release_date': '2026-10-01'},
                         'v34.15': {'release_date': '2026-04-19'}}

    def test_new_versions_are_checked_cached_and_never_switch_connections(self):
        """A future release appears without a manifest edit; repeated checks use cache."""
        settings = self.home / 'localization/workspace/editor-settings.json'
        settings.parent.mkdir(parents=True)
        original = b'{"project_path":"my v35.3 project"}'
        settings.write_bytes(original)
        with patch.object(feed, 'fetch_json', return_value=self.releases) as remote:
            current = feed.catalog(self.home)
            self.assertEqual(current['versions'][0]['version'], 'v35.6')
            self.assertTrue(current['versions'][0]['supported'])
            self.assertFalse(current['versions'][-1]['supported'])
            self.assertEqual(feed.catalog(self.home), current)
            self.assertEqual(remote.call_count, 1)
            feed.catalog(self.home, refresh=True)
            self.assertEqual(remote.call_count, 2)
        self.assertEqual(settings.read_bytes(), original)

    def test_network_or_invalid_feed_keeps_last_valid_list(self):
        """Offline checks show an explicit warning instead of downgrading the saved list."""
        with patch.object(feed, 'fetch_json', return_value=self.releases):
            original = feed.catalog(self.home)
        for error in (OSError('offline'), ValueError('invalid JSON')):
            with patch.object(feed, 'fetch_json', side_effect=error):
                result = feed.catalog(self.home, refresh=True)
                self.assertEqual(result['versions'], original['versions'])
                self.assertEqual(result['checked_at'], original['checked_at'])
                self.assertIn('saved list', result['warning'])
        with patch.object(feed, 'fetch_json', return_value={'../../unsafe': {}}):
            result = feed.catalog(self.home, refresh=True)
            self.assertEqual(result['versions'], original['versions'])
            self.assertTrue(result['warning'])

    def test_skipped_future_releases_keep_each_intermediate_change(self):
        """Updating past a change and revert records both versions, not just their net diff."""
        history = {'releases': {'v35.4': {'commit': '4' * 40, 'changes': {}}}}
        with patch.object(feed, 'read_history', return_value=history), \
             patch.object(feed, 'catalog', return_value={'versions': [
                 {'version': v, 'date': item['release_date'], 'supported': True}
                 for v, item in self.releases.items() if v.startswith('v35.')]}), \
             patch.object(feed, 'source_revision', side_effect=lambda v, home: v[-1] * 40), \
             patch.object(feed, 'release_changes', return_value={'TXT_KEY_ONE': 'updated'}) as compare:
            self.assertEqual(feed.ensure_history('v35.6', self.home), '6' * 40)
            self.assertEqual([call.args[:2] for call in compare.call_args_list],
                             [('4' * 40, '5' * 40), ('5' * 40, '6' * 40)])
        with patch('lekmod_localization.connections.APP_HOME', self.home):
            merged = read_history(self.home)
        self.assertEqual(between(merged, 'v35.4', 'v35.6'), ['v35.5', 'v35.6'])
        for version in ('v35.5', 'v35.6'):
            self.assertEqual(merged['releases'][version]['changes'], {'TXT_KEY_ONE': 'updated'})

    def test_failed_intermediate_comparison_does_not_publish_partial_history(self):
        """A canceled/incomplete comparison cannot make the target version usable."""
        with patch.object(feed, 'read_history', return_value={
            'releases': {'v35.4': {'commit': '4' * 40, 'changes': {}}}}), \
             patch.object(feed, 'catalog', return_value={'versions': [
                 {'version': v, 'date': item['release_date'], 'supported': True}
                 for v, item in self.releases.items() if v.startswith('v35.')]}), \
             patch.object(feed, 'source_revision', side_effect=lambda v, home: v[-1] * 40), \
             patch.object(feed, 'release_changes', side_effect=[{}, OSError('offline')]):
            with self.assertRaises(OSError): feed.ensure_history('v35.6', self.home)
        self.assertFalse((self.home / 'localization/workspace/online-release-history.json').exists())

    def test_comparison_only_reads_changed_english_files_and_normalizes_indent(self):
        """Artwork is never downloaded for comparison; formatting alone is not a text edit."""
        original = b'<GameData><Language_en_US><Row Tag="TXT_KEY_ONE"><Text>Text</Text></Row></Language_en_US></GameData>'
        formatted = original.replace(b'>Text<', b'>\n\t Text\n\t<')
        files = {'status': 'ahead', 'files': [
            {'filename': feed.PRIMARY, 'status': 'modified'},
            {'filename': 'LEKMOD/Art/image.dds', 'status': 'modified'}]}
        with patch.object(feed, 'fetch_json', return_value=files), \
             patch.object(feed, 'fetch_bytes', side_effect=[original, formatted]) as source:
            self.assertEqual(feed.release_changes('4' * 40, '5' * 40), {})
            self.assertEqual(source.call_count, 2)
        changed = original.replace(b'>Text<', b'>New [ICON_CULTURE] text<')
        with patch.object(feed, 'fetch_json', return_value=files), \
             patch.object(feed, 'fetch_bytes', side_effect=[original, changed]):
            self.assertEqual(feed.release_changes('4' * 40, '5' * 40), {'TXT_KEY_ONE': 'updated'})

    def test_current_source_is_pinned_only_after_matching_its_label(self):
        """An available v35.5 cannot point to v35.6 or an unvalidated URL from a feed."""
        with patch.object(feed, 'read_history', return_value={'releases': {}}), \
             patch.object(feed, 'catalog', return_value={'versions': [{'version': 'v35.5', 'supported': True}]}), \
             patch.object(feed, 'fetch_json', side_effect=[{'sha': '6' * 40}, [{'sha': '5' * 40}]]), \
             patch.object(feed, 'fetch_bytes', side_effect=[b'<Text>LEKMOD v35.6</Text>', b'<Text>LEKMOD v35.5</Text>']):
            self.assertEqual(feed.source_revision('v35.5', self.home), '5' * 40)
        with self.assertRaisesRegex(ValueError, 'Invalid Lekmod release'):
            feed.source_revision('../unsafe', self.home)


if __name__ == '__main__': unittest.main()
