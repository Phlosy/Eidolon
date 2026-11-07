Build a small command-line tool and its tests.

Requirements:
1. Create `wc_lite.py`: a CLI (argparse) that takes one file path and prints the number of lines, words and characters of that file, e.g. `3 12 71 notes.txt`. Flags `-l`, `-w`, `-c` restrict the output to lines, words or characters respectively (any combination; no flags means all three). A missing file must print an error to stderr and exit with status 1.
2. Create `test_wc_lite.py` with at least three pytest tests that use `tmp_path` and run the tool through its Python API (a function such as `count(path)`) or via `subprocess` with `sys.executable`.
3. Run the tests with run_tests. If anything fails, fix it and run again.
4. When the tests pass, reply with a short summary: the files you created and what the tests cover.
