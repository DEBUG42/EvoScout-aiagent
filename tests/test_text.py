"""markdown 清洗测试。"""
from app.utils.text import strip_markdown


def test_strip_basic():
    assert strip_markdown("**加粗** 和 `代码`") == "加粗 和 代码"
    assert strip_markdown("## 标题\n正文") == "标题\n正文"
    assert strip_markdown("- 条目1\n- 条目2") == "• 条目1\n• 条目2"
    assert strip_markdown("1. 第一\n2. 第二") == "1. 第一\n2. 第二"


def test_strip_links():
    assert strip_markdown("[论文](https://arxiv.org/abs/1)") == "论文 (https://arxiv.org/abs/1)"


def test_strip_preserves_plain():
    text = "普通文本：CPU 50%，内存 30%。\n第二行"
    assert strip_markdown(text) == text


def test_strip_empty():
    assert strip_markdown("") == ""
    assert strip_markdown(None) is None
