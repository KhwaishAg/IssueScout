"""P1.6: gold labels come from the reference patch (SRS 10.2)."""

from issuescout.evaluation.swebench import (
    changed_old_lines,
    gold_files_from_patch,
    gold_functions,
    patch_files,
)
from issuescout.settings import Settings

SETTINGS = Settings(_env_file=None)

# Base file lines (1-based):
#  1 import os
#  2
#  3 def alpha(x):
#  4     y = x + 1
#  5     return y
#  6
#  7 def beta(x):
#  8     return x * 2
#  9
# 10 class K:
# 11     def method(self):
# 12         return 1
BASE = (
    "import os\n\ndef alpha(x):\n    y = x + 1\n    return y\n\ndef beta(x):\n    return x * 2\n\n"
    "class K:\n    def method(self):\n        return 1\n"
)

REPLACE = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -2,6 +2,6 @@

 def alpha(x):
-    y = x + 1
+    y = x + 2
     return y

 def beta(x):
"""

INSERTION = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -4,3 +4,4 @@
     y = x + 1
+    y *= 3
     return y

"""

TWO_FILES = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
-x = 1
+x = 2
 y = 3
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -5,2 +5,3 @@
 keep
+added
 keep
"""

NEW_AND_DELETED = """diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+a = 1
+b = 2
diff --git a/old.py b/old.py
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone = 1
-gone = 2
"""


def test_files_edited_by_a_patch() -> None:
    assert gold_files_from_patch(REPLACE) == ["pkg/mod.py"]
    assert gold_files_from_patch(TWO_FILES) == ["a.py", "b.py"]


def test_added_files_are_not_gold_but_deleted_files_are() -> None:
    assert patch_files(NEW_AND_DELETED) == [(None, "new.py"), ("old.py", None)]
    assert gold_files_from_patch(NEW_AND_DELETED) == ["old.py"]


def test_replaced_line_is_a_changed_old_line() -> None:
    assert changed_old_lines(REPLACE) == {"pkg/mod.py": {4}}


def test_pure_insertion_attributes_to_the_line_before() -> None:
    assert changed_old_lines(INSERTION) == {"pkg/mod.py": {4}}


def test_multiple_files_and_hunks() -> None:
    assert changed_old_lines(TWO_FILES) == {"a.py": {1}, "b.py": {5}}


def test_deleted_file_lines_are_changed_new_file_is_ignored() -> None:
    assert changed_old_lines(NEW_AND_DELETED) == {"old.py": {1, 2}}


def test_removed_sql_comment_is_not_mistaken_for_a_file_header() -> None:
    patch = (
        "diff --git a/q.sql b/q.sql\n--- a/q.sql\n+++ b/q.sql\n@@ -1,3 +1,3 @@\n"
        " select 1\n--- a comment\n+-- a better comment\n select 2\n"
    )
    assert changed_old_lines(patch) == {"q.sql": {2}}
    assert gold_files_from_patch(patch) == ["q.sql"]


def test_gold_function_is_the_one_containing_the_changed_line() -> None:
    funcs = gold_functions(REPLACE, {"pkg/mod.py": BASE.encode()}, SETTINGS)
    assert funcs == ["pkg/mod.py::pkg.mod.alpha"]


def test_insertion_is_attributed_to_the_function_it_was_added_in() -> None:
    funcs = gold_functions(INSERTION, {"pkg/mod.py": BASE.encode()}, SETTINGS)
    assert funcs == ["pkg/mod.py::pkg.mod.alpha"]


def test_method_inside_a_class() -> None:
    patch = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
        "@@ -11,2 +11,2 @@\n     def method(self):\n-        return 1\n+        return 2\n"
    )
    funcs = gold_functions(patch, {"pkg/mod.py": BASE.encode()}, SETTINGS)
    assert funcs == ["pkg/mod.py::pkg.mod.K.method"]


def test_change_outside_any_function_has_no_gold_function() -> None:
    patch = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
        "@@ -1,2 +1,2 @@\n-import os\n+import sys\n \n"
    )
    assert gold_functions(patch, {"pkg/mod.py": BASE.encode()}, SETTINGS) == []


def test_missing_base_source_is_skipped_not_fatal() -> None:
    assert gold_functions(REPLACE, {}, SETTINGS) == []
