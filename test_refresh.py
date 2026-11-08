import unittest
from unittest.mock import patch
import refresh_live as live


class ValidationTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(live.channel_name(' CCTV-5+ 高清'), 'CCTV5+')
        self.assertEqual(live.channel_name('翡翠台'), 'TVB翡翠台')
        self.assertFalse(live.wanted('支持作者'))

    def test_parse_and_filter(self):
        data = '#EXTM3U\n#EXTINF:-1, CCTV1\nhttps://example.com/a.m3u8\n#EXTINF:-1,广告\nhttps://example.com/ad.m3u8'
        self.assertEqual([x['name'] for x in live.parse_m3u(data, 'test')], ['CCTV1'])

    def test_html_200_is_not_media(self):
        with patch.object(live, 'fetch', return_value=(b'<html>denied</html>', 'https://example.com')):
            result, error = live.probe({'url': 'https://example.com', 'name': 'CCTV1'})
        self.assertIsNone(result)
        self.assertEqual(error, 'ValueError')

    def test_playlist_requires_real_segment(self):
        body = b'#EXTM3U\n#EXTINF:6,\na.ts\n'
        with patch.object(live, 'fetch', side_effect=[(body, 'https://example.com/live.m3u8'), (b'not-video' * 2000, 'https://example.com/a.ts')]):
            result, _ = live.probe({'url': 'https://example.com/live.m3u8', 'name': 'CCTV1'})
        self.assertIsNone(result)

    def test_live_ts_and_relative_url(self):
        body = b'#EXTM3U\n#EXTINF:6,\nsegments/a.ts\n'
        body2 = b'#EXTM3U\n#EXTINF:6,\nsegments/b.ts\n'
        segment = (b'\x47' + bytes(187)) * 100
        with patch.object(live.time, 'sleep'), patch.object(live, 'fetch', side_effect=[(body, 'https://example.com/ch/list.m3u8'), (segment, 'https://example.com/ch/segments/a.ts'), (body2, 'https://example.com/ch/list.m3u8'), (segment, 'https://example.com/ch/segments/b.ts')]) as get:
            result, error = live.probe({'url': 'https://example.com/ch/list.m3u8', 'name': 'CCTV1'})
        self.assertIsNone(error)
        self.assertEqual(result['name'], 'CCTV1')
        self.assertEqual(get.call_args.args[0], 'https://example.com/ch/segments/b.ts')

    def test_stalled_live_playlist_rejected(self):
        body = b'#EXTM3U\n#EXTINF:6,\na.ts\n'
        segment = (b'\x47' + bytes(187)) * 100
        with patch.object(live.time, 'sleep'), patch.object(live, 'fetch', side_effect=[(body, 'https://example.com/list.m3u8'), (segment, 'https://example.com/a.ts'), (body, 'https://example.com/list.m3u8')]):
            result, error = live.probe({'name': 'CCTV1', 'url': 'https://example.com/list.m3u8'})
        self.assertIsNone(result)
        self.assertEqual(error, 'ValueError')

    def test_private_address_rejected(self):
        with patch.object(live.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError):
                live.public_url('http://example.com/a')


if __name__ == '__main__':
    unittest.main()
