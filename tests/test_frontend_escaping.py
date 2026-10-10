"""Runs the real escaping helpers from client/web/static/js/app.js under node and
checks that rendered markup cannot gain extra attributes or non-web links."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "client" / "web" / "static" / "js" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _function_source(source: str, name: str) -> str:
    match = re.search(r"^function %s\(.*?^}\n" % name, source, re.S | re.M)
    assert match, f"{name} not found in app.js"
    return match.group(0)


def _run_node(expressions):
    source = APP_JS.read_text(encoding="utf-8")
    helpers = "".join(_function_source(source, n) for n in ("escapeHtml", "jsArg", "safeLinkHref", "formatMarkdown"))
    script = helpers + "\nconst cases = %s;\nconsole.log(JSON.stringify(cases.map((c) => eval(c))));" % json.dumps(expressions)
    output = subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True).stdout
    return json.loads(output)


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


def _anchors(html):
    parser = _AnchorCollector()
    parser.feed(html)
    return parser.anchors


def test_escape_html_escapes_quotes():
    (escaped,) = _run_node(['escapeHtml(`a"b\'c<d>&`)'])
    assert escaped == "a&quot;b&#39;c&lt;d&gt;&amp;"


def test_markdown_link_cannot_add_attributes():
    text = 'see [report](https://example.com/r" data-x="1)'
    (html,) = _run_node([f"formatMarkdown({json.dumps(text)})"])
    anchors = _anchors(html)
    assert len(anchors) == 1
    assert set(anchors[0]) == {"href", "target", "rel", "class"}
    assert anchors[0]["href"].startswith("https://example.com/r")


def test_markdown_link_only_allows_web_and_mail_schemes():
    rendered = _run_node([
        'formatMarkdown("[a](https://example.com)")',
        'formatMarkdown("[b](mailto:ops@example.com)")',
        'formatMarkdown("[c](data:text/html,x)")',
        'formatMarkdown("[d](  JaVaScRiPt:void(0))")',
    ])
    assert [len(_anchors(html)) for html in rendered] == [1, 1, 0, 0]


def test_js_arg_round_trips_through_html_attribute_decoding():
    tricky = ['plain-id', "it's", 'say "hi"', "back\\slash", "</div><b>"]
    decoded = _run_node([
        "(() => { const v = %s; const attr = jsArg(v);"
        " const js = attr.replace(/&quot;/g, '\"').replace(/&#39;/g, \"'\")"
        ".replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');"
        " return eval(js); })()" % json.dumps(value)
        for value in tricky
    ])
    assert decoded == tricky
