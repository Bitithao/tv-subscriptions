import unittest
from unittest.mock import patch
from unittest.mock import Mock
import refresh_live as live


class ValidationTests(unittest.TestCase):
    def test_media_redirect_closes_page_without_reading_body(self):
        handler=live.SafeRedirect(allow_media_redirects=True)
        handler.parent=Mock()
        request=live.urllib.request.Request('http://example.com/live.m3u8')
        request.timeout=5
        response=Mock()
        with patch.object(live,'public_url'):
            handler.http_error_302(request,response,302,'',{'Location':'https://cdn.example/live.m3u8?token=test'})
        response.read.assert_not_called()
        response.close.assert_called_once()
        self.assertEqual(handler.parent.open.call_args.args[0].full_url,'https://cdn.example/live.m3u8?token=test')

    def test_media_redirect_limit_stops_before_next_request(self):
        handler=live.SafeRedirect(allow_media_redirects=True)
        handler.parent=Mock()
        request=live.urllib.request.Request('http://example.com/live.m3u8')
        request._media_redirect_count=3
        response=Mock()
        with self.assertRaises(ValueError):
            handler.http_error_302(request,response,302,'',{'Location':'https://cdn.example/live.m3u8'})
        response.read.assert_not_called()
        response.close.assert_called_once()
        handler.parent.open.assert_not_called()

    def test_dynamic_media_redirect_still_blocks_quarantined_host(self):
        with patch.object(live.socket,'getaddrinfo') as resolver:
            with self.assertRaises(ValueError):
                live.SafeRedirect(True).redirect_request(live.urllib.request.Request('http://example.com/live.m3u8'),None,302,'',{},'https://kkk.jjjj.jiduo.me/live.m3u8')
        resolver.assert_not_called()

    def test_reviewed_redirect_keeps_media_query(self):
        source='http://74.91.26.218:82/live/gdwshd.m3u8'
        target='http://142.54.179.26:81/live/gdws.m3u8?key=test&tm=123'
        with patch.object(live, 'public_url') as validate:
            request=live.SafeRedirect().redirect_request(live.urllib.request.Request(source),None,302,'',{},target)
        self.assertEqual(request.full_url,target)
        validate.assert_called_once_with(target)

    def test_reviewed_origin_does_not_allow_html_redirect(self):
        with patch.object(live, 'public_url') as validate:
            with self.assertRaises(ValueError):
                live.SafeRedirect().redirect_request(live.urllib.request.Request('http://74.91.26.218:82/a.m3u8'),None,302,'',{},'http://142.54.179.26:81/expired.html')
        validate.assert_not_called()

    def test_unreviewed_redirect_rejected(self):
        with self.assertRaises(ValueError):
            live.SafeRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com/video.m3u8')

    def test_unapproved_origin_or_channel_not_selected(self):
        allowed = {'CCTV1':['https://example.com:443']}
        self.assertTrue(live.approved_candidate({'name':'CCTV1','url':'https://example.com/a.m3u8'},allowed))
        self.assertFalse(live.approved_candidate({'name':'CCTV1','url':'https://unknown.example/a.m3u8'},allowed))
        self.assertFalse(live.approved_candidate({'name':'CCTV2','url':'https://example.com/a.m3u8'},allowed))

    def test_off_origin_segment_not_requested(self):
        body = b'#EXTM3U\n#EXTINF:6,\nhttps://unknown.example/a.ts\n'
        with patch.object(live, 'fetch', return_value=(body, 'https://example.com/live.m3u8')) as get:
            result, error = live.probe({'url':'https://example.com/live.m3u8','name':'CCTV1'})
        self.assertIsNone(result)
        self.assertTrue(error.startswith('ValueError'))
        self.assertEqual(get.call_count,1)

    def test_quarantined_hosts_never_resolve(self):
        with patch.object(live.socket, 'getaddrinfo') as resolver:
            for url in ['http://kkk.jjjj.jiduo.me/a.m3u8', 'https://r.jdshipin.com/a.m3u8']:
                self.assertFalse(live.candidate_allowed(url))
                with self.assertRaises(ValueError):
                    live.public_url(url)
            resolver.assert_not_called()

    def test_redirectors_not_candidates(self):
        self.assertFalse(live.candidate_allowed('http://example.com/api.php?id=1'))
        self.assertFalse(live.candidate_allowed('https://example.com/shortlink'))
        self.assertTrue(live.candidate_allowed('https://example.com/live.m3u8'))

    def test_redirect_to_quarantined_host_blocked(self):
        with self.assertRaises(ValueError):
            live.SafeRedirect().redirect_request(None, None, 302, '', {}, 'http://kkk.jjjj.jiduo.me/a.m3u8')

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
        self.assertTrue(error.startswith('ValueError'))

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
        self.assertTrue(error.startswith('ValueError'))

    def test_private_address_rejected(self):
        with patch.object(live.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError):
                live.public_url('http://example.com/a')


if __name__ == '__main__':
    unittest.main()
