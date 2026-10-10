import unittest
from web.platform import Platform,projection,checked

class AdapterTests(unittest.TestCase):
    def test_normalizes_video_and_rejects_nonplatform_links(self):
        item=projection({'bvid':'BV1234567890','title':'<em>测试</em>视频','owner':{'mid':12,'name':'UP主'},'stat':{'view':100}})
        self.assertEqual(item['title'],'测试视频');self.assertEqual(item['uid'],'12');self.assertEqual(item['views'],100)
        for value in ['https://evil.example/video/BV1234567890','http://www.bilibili.com/video/BV1234567890','https://user:pass@www.bilibili.com/video/BV1234567890']:
            with self.assertRaises(ValueError):Platform.resolve(value)
    def test_business_failure_not_success(self):
        with self.assertRaises(ValueError):checked({'code':-352,'message':'risk'})
