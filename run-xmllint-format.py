#!/usr/bin/env python3
"""A wrapper script around xmllint --format, suitable for linting multiple
files and to use for continuous integration.

This is an alternative API for the xmllint command line.
It runs over multiple files and directories in parallel.
A diff output is produced and a sensible exit code is returned.

Based on https://github.com/DoozyX/clang-format-lint-action.
"""

import argparse
import codecs
import difflib
import fnmatch
import io
import errno
import multiprocessing
import os
import signal
import subprocess
import sys
import traceback

from functools import partial

from subprocess import DEVNULL

DEFAULT_EXTENSIONS = "xml"
DEFAULT_XMLLINT_FORMAT_IGNORE = ".xmllint-format-ignore"


class ExitStatus:
    SUCCESS = 0
    DIFF = 1
    TROUBLE = 2


def excludes_from_file(ignore_file):
    excludes = []
    with io.open(ignore_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                # ignore comments
                continue
            pattern = line.rstrip()
            if not pattern:
                # allow empty lines
                continue
            excludes.append(pattern)
    return excludes


def list_files(files, recursive=False, extensions=None, exclude=None):
    if extensions is None:
        extensions = []
    if exclude is None:
        exclude = []

    out = []
    for file in files:
        if recursive and os.path.isdir(file):
            for dirpath, dnames, fnames in os.walk(file):
                fpaths = [os.path.join(dirpath, fname) for fname in fnames]
                for pattern in exclude:
                    # os.walk() supports trimming down the dnames list
                    # by modifying it in-place,
                    # to avoid unnecessary directory listings.
                    dnames[:] = [
                        x
                        for x in dnames
                        if not fnmatch.fnmatch(os.path.join(dirpath, x), pattern)
                    ]
                    fpaths = [x for x in fpaths if not fnmatch.fnmatch(x, pattern)]
                for f in fpaths:
                    ext = os.path.splitext(f)[1][1:]
                    if ext in extensions:
                        out.append(f)
        else:
            out.append(file)
    return out


def make_diff(file, original, reformatted):
    return list(
        difflib.unified_diff(
            original,
            reformatted,
            fromfile="{}\t(original)".format(file),
            tofile="{}\t(reformatted)".format(file),
            n=3,
        )
    )


class DiffError(Exception):
    def __init__(self, message, errs=None):
        super(DiffError, self).__init__(message)
        self.errs = errs or []


class UnexpectedError(Exception):
    def __init__(self, message, exc=None):
        super(UnexpectedError, self).__init__(message)
        self.formatted_traceback = traceback.format_exc()
        self.exc = exc


def run_xmllint_format_diff_wrapper(args, file):
    try:
        ret = run_xmllint_format_diff(args, file)
        return ret
    except DiffError:
        raise
    except Exception as e:
        raise UnexpectedError("{}: {}: {}".format(file, e.__class__.__name__, e), e)


def run_xmllint_format_diff(args, file):
    with io.open(file, "r", encoding="utf-8") as f:
        original = f.readlines()
    invocation = ["xmllint", "--format", file]

    try:
        proc = subprocess.Popen(
            invocation,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except OSError as exc:
        raise DiffError(
            f"Command '{subprocess.list2cmdline(invocation)}' failed to start: {exc}"
        )
    proc_stdout = proc.stdout
    proc_stderr = proc.stderr
    # hopefully the stderr pipe won't get full and block the process
    outs = list(proc_stdout.readlines())
    errs = list(proc_stderr.readlines())
    proc.wait()
    if proc.returncode:
        raise DiffError(
            f"Command '{subprocess.list2cmdline(invocation)}' returned non-zero exit status {proc.returncode}",
            errs,
        )
    return make_diff(file, original, outs), errs


def bold_red(s):
    return "\x1b[1m\x1b[31m" + s + "\x1b[0m"


def colorize(diff_lines):
    def bold(s):
        return "\x1b[1m" + s + "\x1b[0m"

    def cyan(s):
        return "\x1b[36m" + s + "\x1b[0m"

    def green(s):
        return "\x1b[32m" + s + "\x1b[0m"

    def red(s):
        return "\x1b[31m" + s + "\x1b[0m"

    for line in diff_lines:
        if line[:4] in ["--- ", "+++ "]:
            yield bold(line)
        elif line.startswith("@@ "):
            yield cyan(line)
        elif line.startswith("+"):
            yield green(line)
        elif line.startswith("-"):
            yield red(line)
        else:
            yield line


def print_diff(diff_lines, use_color):
    if use_color:
        diff_lines = colorize(diff_lines)
    if sys.version_info[0] < 3:
        sys.stdout.writelines((l.encode("utf-8") for l in diff_lines))
    else:
        sys.stdout.writelines(diff_lines)


def print_trouble(prog, message, use_colors):
    error_text = "error:"
    if use_colors:
        error_text = bold_red(error_text)
    print(f"{prog}: {error_text} {message}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extensions",
        help=f"comma separated list of file extensions (default: {DEFAULT_EXTENSIONS})",
        default=DEFAULT_EXTENSIONS,
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="run recursively over directories",
    )
    parser.add_argument("files", metavar="file", nargs="+")
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="disable output, useful for the exit code",
    )
    parser.add_argument(
        "-j",
        metavar="N",
        type=int,
        default=0,
        help="run N xmllint jobs in parallel" " (default number of cpus + 1)",
    )
    parser.add_argument(
        "--color",
        default="auto",
        choices=["auto", "always", "never"],
        help="show colored diff (default: auto)",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        metavar="PATTERN",
        action="append",
        default=[],
        help="exclude paths matching the given glob-like pattern(s)"
        " from recursive search",
    )

    args = parser.parse_args()

    # use default signal handling, like diff return SIGINT value on ^C
    # https://bugs.python.org/issue14229#msg156446
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        signal.SIGPIPE
    except AttributeError:
        # compatibility, SIGPIPE does not exist on Windows
        pass
    else:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    colored_stdout = False
    colored_stderr = False
    if args.color == "always":
        colored_stdout = True
        colored_stderr = True
    elif args.color == "auto":
        colored_stdout = sys.stdout.isatty()
        colored_stderr = sys.stderr.isatty()

    retcode = ExitStatus.SUCCESS

    excludes = excludes_from_file(DEFAULT_XMLLINT_FORMAT_IGNORE)
    excludes.extend(args.exclude)

    files = list_files(
        args.files,
        recursive=args.recursive,
        exclude=excludes,
        extensions=args.extensions.split(","),
    )

    if not files:
        print_trouble(parser.prog, "No files found", use_colors=colored_stderr)
        return ExitStatus.TROUBLE

    if not args.quiet:
        print("Processing %s files: %s" % (len(files), ", ".join(files)))

    njobs = args.j
    if njobs == 0:
        njobs = multiprocessing.cpu_count() + 1
    njobs = min(len(files), njobs)

    if njobs == 1:
        # execute directly instead of in a pool,
        # less overhead, simpler stacktraces
        it = (run_xmllint_format_diff_wrapper(args, file) for file in files)
        pool = None
    else:
        pool = multiprocessing.Pool(njobs)
        it = pool.imap_unordered(partial(run_xmllint_format_diff_wrapper, args), files)
    while True:
        try:
            outs, errs = next(it)
        except StopIteration:
            break
        except DiffError as e:
            print_trouble(parser.prog, str(e), use_colors=colored_stderr)
            retcode = ExitStatus.TROUBLE
            sys.stderr.writelines(e.errs)
        except UnexpectedError as e:
            print_trouble(parser.prog, str(e), use_colors=colored_stderr)
            sys.stderr.write(e.formatted_traceback)
            retcode = ExitStatus.TROUBLE
            # stop at the first unexpected error,
            # something could be very wrong,
            # don't process all files unnecessarily
            if pool:
                pool.terminate()
            break
        else:
            sys.stderr.writelines(errs)
            if outs == []:
                continue
            if not args.quiet:
                print_diff(outs, use_color=colored_stdout)
            if retcode == ExitStatus.SUCCESS:
                retcode = ExitStatus.DIFF
    return retcode


if __name__ == "__main__":
    sys.exit(main())
