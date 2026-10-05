import pytest

from categorize.normalize import normalize_merchant


@pytest.mark.parametrize("raw,key", [
    ("MCP-PT Ban Pho 3 (พีที บ้านโพธิ์3)", "mcp pt ban pho"),
    ("MCP-PT Ban Pho 15", "mcp pt ban pho"),
    ("MEB CORPORATION .,LTD", "meb corporation ltd"),
    ("นาย ทดสอบ ระบบ", "ทดสอบ ระบบ"),
    ("นางสาว ทดสอบ ระบบ", "ทดสอบ ระบบ"),
    ("MR. TEST RABOB", "test rabob"),
    ("วัดโสธรวราราม วรวิหาร", "วัดโสธรวราราม วรวิหาร"),  # Thai marks kept
    ("7-ELEVEN #12345", "eleven"),
    ("", None),
    (None, None),
    ("1234", None),
])
def test_normalize(raw, key):
    assert normalize_merchant(raw) == key
