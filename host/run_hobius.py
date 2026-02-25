import urllib.request
import urllib.parse
from html.parser import HTMLParser

class CSRFParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            attr_dict = dict(attrs)
            if attr_dict.get("name") == "csrf":
                self.csrf = attr_dict.get("value", "")

cj = {}

def get_cookies_str():
    return "; ".join([f"{k}={v}" for k, v in cj.items()])

def update_cookies(headers):
    for h in headers.get_all("Set-Cookie", []):
        parts = h.split(";")[0].split("=", 1)
        if len(parts) == 2:
            cj[parts[0]] = parts[1]

def make_req(url, data=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    if cj:
        req.add_header("Cookie", get_cookies_str())
    if data:
        data_enc = urllib.parse.urlencode(data).encode("utf-8")
        req.data = data_enc
        req.method = "POST"
    resp = urllib.request.urlopen(req)
    update_cookies(resp.headers)
    return resp.read().decode("utf-8")

# 1. Login
print("Logging in...")
login_data = {
    "mode": "login",
    "user": "REMOVED",
    "password": "REMOVED",
    "submit": "login" # Sometimes it's the button value
}
try:
    make_req("https://example.org/account/signin.php", login_data)
except Exception as e:
    pass

# 2. Get the homepage
print("Fetching homepage...")
html = make_req("https://example.org/")
if "REMOVED" in html.lower() or "logout" in html.lower():
    print("Login successful.")
else:
    print("Login failed.")

print("Trying to post...")
# Find the CSRF token if any, or post form action
parser = CSRFParser()
parser.feed(html)

post_data = {
    "text": "NAVIG Cognitive Post: The OODA Loop has natively injected this post via pure HTTP orchestration.",
    "submit": "post"
}
if parser.csrf:
    post_data["csrf"] = parser.csrf

try:
    make_req("https://example.org/post", post_data)
    print("Post sent!")
except Exception as e:
    print(e)
