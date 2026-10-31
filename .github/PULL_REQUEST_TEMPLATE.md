<!--
Thanks for contributing! Pull requests go to the `develop` branch.
Please read the contributing guidelines before you open one:
https://github.com/timothestoifl24/bulk-mailer/blob/develop/CONTRIBUTING.md

This template is for pull requests into `develop`. Releases (develop → main) and hotfixes
(hotfix/* → main) don't need it: replace it with a short description.
-->

## What and why

<!-- What does this change, and why is it needed? Link the issue: "Closes #123". -->

## Type of change

<!-- Tick all that apply (at least one). The pull request is labelled automatically from these boxes. -->

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Documentation update
- [ ] Code refactoring
- [ ] API changes
- [ ] Performance improvement
- [ ] Test addition or update

## AI usage

<!-- Tick what applies; leave both empty if no AI was involved. -->

- [ ] AI was used to write this
- [ ] AI was used to review this

## How it was tested

<!-- Tests added or updated, and anything you checked by hand. -->

## Checklist

- [ ] `python -m pytest -q` passes, and new behaviour has a test (a bug fix has one that fails without the fix)
- [ ] If models, queries or the sending worker changed: the suite also passes against PostgreSQL
- [ ] A line under `## [Unreleased]` in `CHANGELOG.md` if users notice the change
- [ ] `README.md` and `docs/` are updated if a documented setting, endpoint or workflow changed
- [ ] No secrets, `.env` or local database files are committed

## Contributing guidelines

<!-- Required: the "Guidelines accepted" check stays red until this box and at least one type of change are ticked. -->

- [ ] I have read and accept the [contributing guidelines](https://github.com/timothestoifl24/bulk-mailer/blob/develop/CONTRIBUTING.md), including that my contribution is licensed under the MIT License.
