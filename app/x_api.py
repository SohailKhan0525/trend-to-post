"""One authenticated request to the official X create-post endpoint."""
import os


class RejectedPost(Exception):
    def __init__(self, status):
        self.status = status


def prepare_sender():
    from requests_oauthlib import OAuth1Session
    auth = OAuth1Session(
        os.environ["X_API_KEY"], client_secret=os.environ["X_API_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )

    def send(text):
        with auth:
            response = auth.post("https://api.x.com/2/tweets", json={"text":text},
                                 timeout=(10,30), allow_redirects=False)
        if response.status_code in {400,401,402,403,404,422,429}:
            raise RejectedPost(response.status_code)
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("Unconfirmed response to create-post")
        ident = response.json()["data"]["id"]
        if not isinstance(ident,str) or not ident.isdigit():
            raise ValueError("Unconfirmed post ID")
        return ident

    return send
