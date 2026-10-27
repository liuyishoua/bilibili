import tempfile
import unittest
from unittest.mock import patch
from web.platform import Platform,PlatformError

class ResearchToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.platform=Platform(self.tmp.name);self.platform.client=lambda:object()
    def test_profile_is_a_research_record(self):
        with patch('web.platform.BiliApi.get_user_info',return_value={'code':0,'data':{'mid':123,'name':'UP','sign':'<b>介绍</b>','official':{'title':'认证'},'level':6}}) as call:
            item=self.platform.tool('profile',{'uid':'123'})['items'][0]
            self.assertEqual(item['kind'],'profile');self.assertEqual(item['uid'],'123');self.assertIn('介绍',item['text'])
            call.assert_called_once()
    def test_room_failure_and_invalid_ids_do_not_become_records(self):
        for value in ['','https://evil.example','-1','1'*25]:
            with self.assertRaises(PlatformError):self.platform.tool('live',{'room_id':value})
        with patch('apis.bili_live_apis.BiliLiveApi.get_room_info',return_value={'code':-1}):
            with self.assertRaises(PlatformError):self.platform.tool('live',{'room_id':'123'})
    def test_live_record_uses_room_identity(self):
        with patch('apis.bili_live_apis.BiliLiveApi.get_room_info',return_value={'code':0,'data':{'room_info':{'room_id':99,'uid':12,'title':'直播','live_status':1,'online':321},'anchor_info':{'base_info':{'uname':'主播'}}}}):
            item=self.platform.tool('live',{'room_id':'99'})['items'][0]
            self.assertEqual(item['kind'],'live');self.assertEqual(item['views'],321);self.assertEqual(item['url'],'https://live.bilibili.com/99')
    def test_recommendation_ignores_nonvideo_cards(self):
        with patch('web.platform.BiliApi.get_rcmd_feed',return_value={'code':0,'data':{'item':[{'bvid':'BV1234567890','title':'视频'},{'id':3}]}}):
            self.assertEqual(len(self.platform.tool('recommend',{})['items']),1)
