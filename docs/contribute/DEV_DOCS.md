# Augur Development Docs for Contributors

Thank you for helping us to improve Augur! This document describes:

- Getting Started
- Contributing code
  - Running local code changes
  - Testing
  - Releasing
  - Maintaining Bioconda package
  - Continuous integration
- Contributing documentation
  - Formats (Markdown and reStructuredText)
  - Documentation structure
  - Building documentation

## Getting started

To be an effective, productive contributor, please start by reading the
[**Nextstrain contributing guide**](https://github.com/nextstrain/.github/blob/master/CONTRIBUTING.md)
for useful information about how to pick an issue, submit your contributions, and so on.

This project strictly adheres to the
[Contributor Covenant Code of Conduct](https://github.com/nextstrain/.github/blob/master/CODE_OF_CONDUCT.md).

Please see the [project board](https://github.com/orgs/nextstrain/projects/6) for currently available issues.

## Contributing code

We currently target compatibility with Python 3.7 and higher. As Python releases new versions,
the minimum target compatibility may be increased in the future.

### Running local changes

While you are making code changes, you will want to run augur to see it behavior with those changes.
To to test your local changes (without installing them to your system), run the following
**convenience script** from the root of your cloned git repository:

```bash
./bin/augur
```

Note that the `./bin/augur` convenience script is not installing `augur` system-wide with pip.

As an alternative to using the convenience script and to install the dev dependencies, you can install augur from source
as an **editable package** so that your global `augur` command always uses your
local source code copy:

```bash
pip install -e '.[dev]'
```

Using an "editable package" is not recommended if you want to be able to compare output
from a stable, released version of augur with your development version (e.g. comparing
output of `augur` installed with pip and `./bin/augur` from your local source code).

### Testing

Writing good tests and running tests helps maintain code quality and eases future refactoring.
We use [pytest](https://docs.pytest.org) and [Cram](https://bitheap.org/cram/) to test augur.
This section will describe briefly:

- Writing tests
  - Unit tests
  - Doctests
  - Functional tests
- Running tests
  - Locally
  - Continuous Integration

#### Writing Tests

It's good practice to write **unit tests** for any code contribution.
The [pytest documentation](https://docs.pytest.org) and [Python documentation](https://docs.python.org) are good references for unit tests.
Augur's unit tests are located in the `tests` directory and there is generally one test file for each code file.

On the other hand, [**doctests**](https://docs.python.org/3/library/doctest.html) are a type of tests that are written within a module's docstrings.
They can be helpful for testing a real-world example and determining if a regression is introduced in a particular module.

A pull request that contributes new code **should always contain unit tests**.
Optionally, a pull request may also contain doctests if the contributor believes a doctest would improve the documentation and execution of a real world example.

We test augur's command line interface with functional tests implemented with the [Cram framework](https://bitheap.org/cram/).
These tests complement existing unit tests of individual augur Python functions by running augur commands on the shell and confirming that these commands:

1. execute without any errors
2. produce exactly the expected outputs for the given inputs

These tests can reveal bugs resulting from untested internal functions or untested combinations fo internal functions.

Functional tests should either:

* suitably test a single augur command with an eponymously named Cram file in `tests/functional/` (e.g., `mask.t` for augur mask)

OR

* test a complete build with augur commands with an appropriately named Cram file in `tests/builds/` (e.g., `zika.t` for the example Zika build)

##### Functional tests of specific commands

Functional tests of specific commands consist of a single Cram file per test and a corresponding directory of expected inputs and outputs to use for comparison of test results.

The Cram file should test most reasonable combinations of command arguments and flags.

##### Functional tests of example builds

Functional tests of example builds use output from a real Snakemake workflow as expected inputs and outputs.
These tests should confirm that all steps of a workflow can execute and produce the expected output.
These tests reflect actual augur usage in workflows and are not intended to comprehensively test interfaces for specific augur commands.

The Cram file should replicate the example workflow from start to end.
These tests should use the output of the Snakemake workflow (e.g., files in `zika/results/` for the Zika build test) as the expected inputs and outputs.

##### Comparing outputs of augur commands

Compare deterministic outputs of augur commands with a `diff` between the expected and observed output files.
For extremely simple deterministic outputs, use the expected text written to standard output instead of creating a separate expected output file.

To compare trees with stochastic branch lengths:

1. provide a fixed random seed to the tree builder executable (e.g., `--tree-builder-args "-seed 314159"` for the “iqtree” method of augur tree)
2. use `scripts/diff_trees.py` instead of `diff` and optionally provide a specific number to  `--significant-digits` to limit the precision that should be considered in the diff

To compare JSON outputs with stochastic numerical values, use `scripts/diff_jsons.py` with the appropriate `--significant-digits` argument.

Both tree and JSON comparison scripts rely on [deepdiff](https://deepdiff.readthedocs.io/en/latest/) for underlying comparisons.

##### Tips for writing Cram tests

Cram is a simple testing framework that is also very versatile. Over time, we have changed the way we design and organize Augur's Cram tests. You might find older practices in existing tests that haven't been updated yet, but these are the latest guidelines that we've discovered to be helpful.

1. Keep cram files modular. This makes it easier to see which command is failing.
2. Create files in the initial working directory, as it is a temporary working directory unique to the test. Note that the name of the `$TMP` directory is misleading - although it is temporary, it is shared across all tests so you'll have to explicitly remove files at the end of each test to avoid affecting other tests. The initial directory of each test is a unique directory within `$TMP`.

#### Running Tests

You've written tests and now you want to run them to see if they are passing.
First, you will need to [install the complete Nextstrain environment](https://nextstrain.org/docs/getting-started/local-installation) and augur dev dependencies as described above.
Next, run all augur tests with the following command from the root, top-level of the augur repository:

```bash
./run_tests.sh
```

For rapid execution of a subset of unit tests (as during test-driven development), the `-k` argument will disable code coverage and functional tests and pass directly to pytest to limit the tests that are run.
For example, the following command only runs unit tests related to augur mask.

```bash
./run_tests.sh -k test_mask
```

To run a specific integration test with cram, you can use the following command:

```bash
cram tests/functional/clades.t
```

Troubleshooting tip: As tests run on the development code in the augur repository, your environment should not have an existing augur installation that could cause a conflict in pytest.

We use continuous integration with GitHub Actions to run tests on every pull request submitted to the project.
We use [codecov](https://codecov.io/) to automatically produce test coverage for new contributions and the project as a whole.

### Releasing

Versions for this project, Augur, from 3.0.0 onwards aim to follow the
[Semantic Versioning rules](https://semver.org).

#### Steps

##### 1. Gather PRs.

1. Compare changes to find PRs and direct commits since the previous tag (replacing `X.X.X` with previous tag)
    - For all changes: open https://github.com/nextstrain/augur/compare/X.X.X...HEAD
    - For PRs: run the commands below and paste the output URL in your browser:
      ```sh
      previous_tag="X.X.X"
      previous_tag_date=$(git log -1 $previous_tag --format=%cd --date=format:'%Y-%m-%dT%H:%M:%SZ')
      echo "https://github.com/nextstrain/augur/pulls?q=is:pr%20is:closed%20merged:>$previous_tag_date"
      ```
2. Define a new version number `X.X.X` based on changes and Semantic Versioning rules.

##### 2. Curate [CHANGES.md](../../CHANGES.md)

1. Go through each PR and note the PRs that didn't provide an update to [CHANGES.md](../../CHANGES.md).
2. For the PRs missing a changelog update, add an entry summarizing the changes in the PR.
    - Keep headers and formatting consistent with the rest of the file.
3. Open a PR with these changes. If changes are clear and you feel confident in the release notes, merge without PR approval. Otherwise, or if unsure, add [nextstrain/core](https://github.com/orgs/nextstrain/teams/core) as a reviewer and wait for approval before proceeding with the release.

##### 3. Run build/test/release scripts

1. Go to [this GitHub Actions workflow](https://github.com/nextstrain/augur/actions/workflows/release.yaml).
2. Select **Run workflow**. In the new menu:
    1. Ensure `master` branch is selected.
    2. In **New version X.X.X**, provide the new version number.
    3. Select **Run workflow**.
3. Ensure workflow runs successfully.
    - Ensure the [docker-base CI action triggered by nextstrain-bot](https://github.com/nextstrain/docker-base/actions/workflows/ci.yml?query=branch%3Amaster+actor%3Anextstrain-bot) runs successfully.

##### 4. Update on Bioconda

First, check if the dependency list in [setup.py](https://github.com/nextstrain/augur/blob/HEAD/setup.py) had any changes since the previous version.

For versions without dependency changes:

1. Wait for an auto-bump PR in [bioconda-recipes][].
2. Add a comment `@BiocondaBot please add label`.
3. Wait for a bioconda maintainer to approve and merge.

For versions with dependency changes:

1. Create a new PR in [bioconda-recipes][] following instructions at [nextstrain/bioconda-recipes/README.md](https://github.com/nextstrain/bioconda-recipes/blob/readme/README.md).
    - [Example](https://github.com/bioconda/bioconda-recipes/pull/34344)
2. Add a comment `@BiocondaBot please add label`.
3. Wait for a bioconda maintainer to approve and merge.
4. Wait for an auto-bump PR in [bioconda-recipes][].
5. Add a comment in the auto-bump PR `Please close this in favor of #<your PR number>`.

[bioconda-recipes]: https://github.com/bioconda/bioconda-recipes/pull/34509

##### 5. Build/Release Nextstrain/conda-base

1. Wait for the bioconda-recipe PR to be merged.
2. Wait for the new version of Augur to be available in on bioconda.
3. Manually run the [conda-base CI workflow](https://github.com/nextstrain/conda-base/actions/workflows/ci.yaml) on the `main` branch.
4. Ensure workflow runs successfully.

#### Notes

New releases are tagged in git using an "annotated" tag.  If the git option
`user.signingKey` is set, the tag will also be [signed][].  Signed tags are
preferred, but it can be hard to setup GPG correctly.  Source and wheel
(binary) distributions are uploaded to [the nextstrain-augur project on
PyPi](https://pypi.org/project/nextstrain-augur).

There is a `./devel/release` script which will prepare a new release from your
local repository.  It ends with instructions for you on how to push the release
commit/tag/branch and how to upload the built distributions to PyPi.  You'll
need [a PyPi account][] and [twine][] installed to do the latter.

After you create a new release and before you push it to GitHub, run all tests again as described above to confirm that nothing broke with the new release.
If any tests fail, run the `./devel/rewind-release` script to undo the release, then fix the tests before trying again.

New releases trigger a new [docker-base][] build to keep the Docker image
up-to-date. This trigger is implemented as the _rebuild-docker-image_ job in
the release workflow, which is explicitly conditioned on the previous _run_
job's successful completion. To trigger a Docker image rebuild without making a
release, see [this section of the docker-base README](https://github.com/nextstrain/docker-base#rebuilding-an-image-and-pushing-to-docker-hub).

[signed]: https://git-scm.com/book/en/v2/Git-Tools-Signing-Your-Work
[a PyPi account]: https://pypi.org/account/register/
[twine]: https://pypi.org/project/twine
[docker-base]: https://github.com/nextstrain/docker-base

### Maintaining Bioconda package

Bioconda hosts [augur’s conda package](http://bioconda.github.io/recipes/augur/README.html) and defines augur’s dependencies in [a conda recipe YAML file](https://github.com/bioconda/bioconda-recipes/blob/master/recipes/augur/meta.yaml).
New releases on GitHub automatically trigger a new Bioconda release.

To modify augur’s dependencies or other aspects of its conda environment, [follow Bioconda’s contributing guide](https://bioconda.github.io/contributor/index.html).
You will need to update the existing recipe YAML locally and create a pull request on GitHub for testing and review.
Add your GitHub username to the `recipe_maintainers` list, if this is your first time modifying the augur recipe.
After a successful pull request review, Bioconda will automatically update the augur package that users download.

### Continuous Integration (CI)

Branches and PRs are tested by GitHub Actions workflows configured in `.github/workflows`.

Our CI GitHub Actions workflow is comprised of a _test_ job that runs tests and uploads the coverage report to Codecov.
Currently, only `pytest` results are included in the report.

## Contributing documentation

[Documentation](https://docs.nextstrain.org/projects/augur) is built using [Sphinx](http://sphinx-doc.org/) and hosted on [Read The Docs](https://readthedocs.org/).
Versions of the documentation for each augur release and git branch are available and preserved.
Read The Docs is updated automatically from commits and releases on GitHub.

### Doc Formats

Documentation is mostly written as [reStructuredText](http://www.sphinx-doc.org/en/master/usage/restructuredtext/index.html) (.rst) files, but they can also be Markdown (.md) files.
There are advantages to both formats:

- reStructuredText enables python-generated text to fill your documentation as in the
  auto-importing of modules or usage of plugins like `sphinx-argparse` (see below).
- Markdown is more intuitive to write and is widely used outside of python development.
- If you don't need autogeneration of help documentation, then you may want to stick with writing Markdown.

Sphinx, coupled with reStructuredText, can be tricky to learn.
Here's a [subset of reStructuredText worth committing to memory](https://simonwillison.net/2018/Aug/25/restructuredtext/) to help you get started writing these files.

Many Sphinx reStructuredText files contain a [directive](http://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html) to add relations between single files in the documentation known as a Table of Contents Tree ([TOC Tree](https://documentation.help/Sphinx/toctree.html)).

Human-readable augur and augur subcommand documentation is written using a Sphinx extension called [sphinx-argparse](https://sphinx-argparse.readthedocs.io/en/latest/index.html).

### Folder structure

The documentation source-files are located in `./docs`, with `./docs/index.rst` being the main entry point.
Each subsection of the documentation is a subdirectory inside `./docs`.
For instance, the tutorials are all found in `./docs/tutorials` and are included in the documentation website via the directive in `./docs/index.rst`.

### Building documentation

Building the documentation locally is useful to test changes.
First, make sure you have the development dependencies of augur installed:

```bash
pip install -e '.[dev]'
```

This installs packages listed in the `dev` section of `extras_require` in _setup.py_,
as well as augur's dependencies as necessary.

Sphinx and make are used when **building** documentation. Here are some examples that you
may find useful:

Build the HTML output format by running:

```bash
make -C docs html
```

To monitor the source files for changes and automatically rebuild as necessary, run:

```bash
make -C docs livehtml
```

Sphinx can build other formats, such as epub. To see other available formats, run:

```bash
make -C docs help
```

To update the API documentation after adding or removing an augur submodule,
autogenerate a new API file as follows.

```bash
sphinx-apidoc -T -f -MeT -o docs/api augur
```

To make doc rebuilds faster, Sphinx caches built documentation by default,
which is generally great, but can cause the sidebar of pages to be stale.
You can clean out the cache with:

```bash
make -C docs clean
```

To **view** the generated documentation in your browser, Mac users should run:

```bash
open docs/_build/html/index.html
```

Linux users can **view** the docs by running:

```bash
xdg-open docs/_build/html/index.html
```

This will open your browser for you to see and read your work.
