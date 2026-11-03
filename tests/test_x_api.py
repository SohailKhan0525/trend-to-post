import os
import sys
from unittest import TestCase
from unittest.mock import MagicMock, patch
from app.x_api import prepare_sender, RejectedPost

class XApiTests(TestCase):
    def test_official_post_is_one_plain_text_request(self):
        library=MagicMock()
        session=library.OAuth1Session.return_value
        reply=session.post.return_value
        reply.status_code=201
        reply.json.return_value={"data":{"id":"123"}}
        names=("X_API_KEY","X_API_SECRET","X_ACCESS_TOKEN","X_ACCESS_TOKEN_SECRET")
        with patch.dict(sys.modules,{"requests_oauthlib":library}),patch.dict(os.environ,dict.fromkeys(names,"fixture")):
            send=prepare_sender()
            self.assertEqual(send("hello rain"),"123")
            session.post.assert_called_once_with("https://api.x.com/2/tweets",json={"text":"hello rain"},timeout=(10,30),allow_redirects=False)
            reply.status_code=402
            with self.assertRaises(RejectedPost):
                prepare_sender()("another post")
