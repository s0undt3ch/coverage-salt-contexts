# pylint: disable=missing-module-docstring
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile

import nox  # pylint: disable=import-error
from nox.command import CommandFailed  # pylint: disable=import-error


IS_WINDOWS = sys.platform.lower().startswith("win")

if not IS_WINDOWS:
    COVERAGE_FAIL_UNDER_PERCENT = 80
else:
    COVERAGE_FAIL_UNDER_PERCENT = 70

# Be verbose when running under a CI context
PIP_INSTALL_SILENT = (
    os.environ.get("JENKINS_URL")
    or os.environ.get("CI")
    or os.environ.get("DRONE")
    or os.environ.get("GITHUB_ACTIONS")
) is None
CI_RUN = PIP_INSTALL_SILENT is False
SKIP_REQUIREMENTS_INSTALL = "SKIP_REQUIREMENTS_INSTALL" in os.environ
EXTRA_REQUIREMENTS_INSTALL = os.environ.get("EXTRA_REQUIREMENTS_INSTALL")

# Paths
REPO_ROOT = pathlib.Path(__file__).resolve().parent
# Change current directory to REPO_ROOT
os.chdir(str(REPO_ROOT))

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
# Make sure the artifacts directory exists
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
RUNTESTS_LOGFILE = ARTIFACTS_DIR.relative_to(REPO_ROOT) / "runtests-{}.log".format(
    datetime.datetime.now().strftime("%Y%m%d%H%M%S.%f")
)
COVERAGE_REPORT_DB = REPO_ROOT / ".coverage"
COVERAGE_REPORT_PROJECT = ARTIFACTS_DIR.relative_to(REPO_ROOT) / "coverage-project.xml"
COVERAGE_REPORT_TESTS = ARTIFACTS_DIR.relative_to(REPO_ROOT) / "coverage-tests.xml"
JUNIT_REPORT = ARTIFACTS_DIR.relative_to(REPO_ROOT) / "junit-report.xml"

# Nox options
#  Reuse existing virtualenvs
nox.options.reuse_existing_virtualenvs = True
#  Don't fail on missing interpreters
nox.options.error_on_missing_interpreters = False


@nox.session(python=("3", "3.5", "3.6", "3.7", "3.8", "3.9"))
def tests(session):  # pylint: disable=too-many-branches
    """
    Run tests
    """
    env = {}
    if SKIP_REQUIREMENTS_INSTALL is False:
        # Always have the wheel package installed
        session.install("wheel", silent=PIP_INSTALL_SILENT)
        session.install("-e", ".", silent=PIP_INSTALL_SILENT)

        if EXTRA_REQUIREMENTS_INSTALL:
            session.log(
                "Installing the following extra requirements because the EXTRA_REQUIREMENTS_INSTALL "
                "environment variable was set: EXTRA_REQUIREMENTS_INSTALL='%s'",
                EXTRA_REQUIREMENTS_INSTALL,
            )
            install_command = ["--progress-bar=off"]
            install_command += [req.strip() for req in EXTRA_REQUIREMENTS_INSTALL.split()]
            session.install(*install_command, silent=PIP_INSTALL_SILENT)

    session.run("coverage", "erase")

    sitecustomize_dir = session.run_always(
        "pytest-coverage-context", "--coverage", silent=True, log=False, stderr=None
    ).strip()

    python_path_env_var = os.environ.get("PYTHONPATH") or None
    if python_path_env_var is None:
        python_path_env_var = sitecustomize_dir
    else:
        python_path_entries = python_path_env_var.split(os.pathsep)
        if sitecustomize_dir in python_path_entries:
            python_path_entries.remove(sitecustomize_dir)
        python_path_entries.insert(0, sitecustomize_dir)
        python_path_env_var = os.pathsep.join(python_path_entries)

    env.update(
        {
            # The updated python path so that sitecustomize is importable
            "PYTHONPATH": python_path_env_var,
            # The full path to the .coverage data file. Makes sure we always write
            # them to the same directory
            "COVERAGE_FILE": str(COVERAGE_REPORT_DB),
            # Instruct sub processes to also run under coverage
            "COVERAGE_PROCESS_START": str(REPO_ROOT / ".coveragerc"),
        }
    )

    args = [
        "--rootdir",
        str(REPO_ROOT),
        "--log-file={}".format(RUNTESTS_LOGFILE),
        "--log-file-level=debug",
        "--show-capture=no",
        "--junitxml={}".format(JUNIT_REPORT),
        "--showlocals",
        "-ra",
        "-s",
    ]
    # pylint: disable=protected-access
    if session._runner.global_config.forcecolor:
        args.append("--color=yes")
    if not session.posargs:
        args.append("tests/")
    else:
        for arg in session.posargs:
            if arg.startswith("--color") and session._runner.global_config.forcecolor:
                args.remove("--color=yes")
            args.append(arg)
    # pylint: enable=protected-access

    session.run("coverage", "run", "-m", "pytest", *args, env=env)

    # Always combine and generate the XML coverage report
    try:
        session.run("coverage", "combine")
    except CommandFailed:
        # Sometimes some of the coverage files are corrupt which would
        # trigger a CommandFailed exception
        pass
    # Generate report for coveragectx code coverage
    session.run(
        "coverage",
        "xml",
        "-o",
        str(COVERAGE_REPORT_PROJECT),
        "--omit=tests/*",
        "--include=coveragectx/*",
    )
    # Generate report for tests code coverage
    session.run(
        "coverage",
        "xml",
        "-o",
        str(COVERAGE_REPORT_TESTS),
        "--omit=coveragectx/*",
        "--include=tests/*",
    )
    try:
        cmdline = ["coverage", "report", "--show-missing", "--include=coveragectx/*,tests/*"]
        session.run(*cmdline)
    finally:
        if COVERAGE_REPORT_DB.exists():
            shutil.copyfile(str(COVERAGE_REPORT_DB), str(ARTIFACTS_DIR / ".coverage"))


def _lint(session, rcfile, flags, paths):
    session.install(
        "--progress-bar=off",
        "-r",
        os.path.join("requirements", "lint.txt"),
        silent=PIP_INSTALL_SILENT,
    )
    session.run("pylint", "--version")
    pylint_report_path = os.environ.get("PYLINT_REPORT")

    cmd_args = ["pylint", "--rcfile={}".format(rcfile)] + list(flags) + list(paths)

    stdout = tempfile.TemporaryFile(mode="w+b")
    try:
        session.run(*cmd_args, stdout=stdout)
    finally:
        stdout.seek(0)
        contents = stdout.read()
        if contents:
            contents = contents.decode("utf-8")
            sys.stdout.write(contents)
            sys.stdout.flush()
            if pylint_report_path:
                # Write report
                with open(pylint_report_path, "w") as wfh:
                    wfh.write(contents)
                session.log("Report file written to %r", pylint_report_path)
        stdout.close()


@nox.session(python="3")
def lint(session):
    """
    Run PyLint against Salt and it's test suite. Set PYLINT_REPORT to a path to capture output.
    """
    session.notify("lint-code-{}".format(session.python))
    session.notify("lint-tests-{}".format(session.python))


@nox.session(python="3", name="lint-code")
def lint_code(session):
    """
    Run PyLint against the code. Set PYLINT_REPORT to a path to capture output.
    """
    flags = ["--disable=I"]
    if session.posargs:
        paths = session.posargs
    else:
        paths = ["setup.py", "noxfile.py", "coveragectx/"]
    _lint(session, ".pylintrc", flags, paths)


@nox.session(python="3", name="lint-tests")
def lint_tests(session):
    """
    Run PyLint against Salt and it's test suite. Set PYLINT_REPORT to a path to capture output.
    """
    flags = ["--disable=I"]
    if session.posargs:
        paths = session.posargs
    else:
        paths = ["tests/"]
    _lint(session, ".pylintrc", flags, paths)


@nox.session(python="3")
def docs(session):
    """
    Build Docs
    """
    session.install(
        "--progress-bar=off",
        "-r",
        os.path.join("requirements", "docs.txt"),
        silent=PIP_INSTALL_SILENT,
    )
    os.chdir("docs/")
    session.run("make", "clean", external=True)
    session.run("make", "linkcheck", "SPHINXOPTS=-W", external=True)
    session.run("make", "coverage", "SPHINXOPTS=-W", external=True)
    docs_coverage_file = os.path.join("_build", "html", "python.txt")
    if os.path.exists(docs_coverage_file):
        with open(docs_coverage_file) as rfh:
            contents = rfh.readlines()[2:]
            if contents:
                session.error("\n" + "".join(contents))
    session.run("make", "html", "SPHINXOPTS=-W", external=True)
    os.chdir("..")


@nox.session(name="docs-crosslink-info", python="3")
def docs_crosslink_info(session):
    """
    Report intersphinx cross links information
    """
    session.install(
        "--progress-bar=off",
        "-r",
        os.path.join("requirements", "docs.txt"),
        silent=PIP_INSTALL_SILENT,
    )
    os.chdir("docs/")
    intersphinx_mapping = json.loads(
        session.run(
            "python",
            "-c",
            "import json; import conf; print(json.dumps(conf.intersphinx_mapping))",
            silent=True,
            log=False,
        )
    )
    try:
        mapping_entry = intersphinx_mapping[session.posargs[0]]
    except IndexError:
        session.error(
            "You need to pass at least one argument whose value must be one of: {}".format(
                ", ".join(list(intersphinx_mapping))
            )
        )
    except KeyError:
        session.error(
            "Only acceptable values for first argument are: {}".format(
                ", ".join(list(intersphinx_mapping))
            )
        )
    session.run(
        "python", "-m", "sphinx.ext.intersphinx", mapping_entry[0].rstrip("/") + "/objects.inv"
    )
    os.chdir("..")


@nox.session(name="gen-api-docs", python="3")
def gen_api_docs(session):
    """
    Generate API Docs
    """
    session.install(
        "--progress-bar=off",
        "-r",
        os.path.join("requirements", "docs.txt"),
        silent=PIP_INSTALL_SILENT,
    )
    shutil.rmtree("docs/ref")
    session.run("sphinx-apidoc", "--module-first", "-o", "docs/ref/", "coveragectx/")
