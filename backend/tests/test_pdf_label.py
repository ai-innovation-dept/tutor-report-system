from app.api.reports import _tutor_label


class _Tutor:
    def __init__(self, name, no):
        self.display_name = name
        self.user_no = no


class _Report:
    def __init__(self, tutor):
        self.tutor = tutor


def test_tutor_label_with_id():
    assert _tutor_label(_Report(_Tutor("大橋悟史", "10003"))) == "大橋悟史（10003）"


def test_tutor_label_without_id():
    assert _tutor_label(_Report(_Tutor("講師A", None))) == "講師A"


def test_tutor_label_no_tutor():
    assert _tutor_label(_Report(None)) == "講師未設定"
