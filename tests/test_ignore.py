from callcoach.ignore import ignore_path, load_ignored


def test_the_names_i_know_are_the_lines_of_the_file(tmp_path):
    ignore_path(tmp_path).write_text("# לא להקפיץ\n\nשמות שאני מכיר.\n\n- Okta\n-  SOC 2 \n- \nOkta בלי מקף\n",
                                     encoding="utf-8")
    assert load_ignored(tmp_path) == ("Okta", "SOC 2")


def test_no_file_is_no_names(tmp_path):
    assert load_ignored(tmp_path) == ()
