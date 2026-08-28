"""点击追踪测试（测试文档 §4.12 T-TRACK-01~13；全部 respx mock，禁真实网络）。"""

from __future__ import annotations

from daily_picks.tracking import (
    CODE_ALPHABET,
    CODE_LENGTH,
    TrackingError,
    build_tracking_url,
    gen_code,
)


class TestPureFunctions:
    # T-TRACK-01：短码格式——8 位、仅 base62 字符、随机不重复
    def test_gen_code_format(self):
        code = gen_code()
        assert len(code) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in code)
        assert gen_code() != gen_code()  # 随机性（碰撞概率可忽略）

    # T-TRACK-01b：gen_code 尊重显式长度
    def test_gen_code_custom_length(self):
        assert len(gen_code(6)) == 6

    # T-TRACK-02：短链构造——base 去尾斜杠 + /c/{code}
    def test_build_tracking_url(self):
        assert build_tracking_url("https://track.example.workers.dev/", "abcd1234") == \
            "https://track.example.workers.dev/c/abcd1234"
        assert build_tracking_url("https://track.example.workers.dev", "abcd1234") == \
            "https://track.example.workers.dev/c/abcd1234"

    def test_tracking_error_is_exception(self):
        assert issubclass(TrackingError, Exception)
