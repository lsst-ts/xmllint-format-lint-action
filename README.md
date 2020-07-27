# xmllint-format lint action

This action checks if the XML files matches xmllint formatting. Based on
[clang-format](https://github.com/DoozyX/clang-format-lint-action).

## Inputs

### `source`

Where the XML files are located.\
Default: '.' (current folder)\
Example: './interfaces'

### `exclude`

What folder should be excluded from format checking.\
Default: 'none'\
Example: './third_party'

### `extensions`

What filename extensions should be used for format checking.\
Default: 'xml'\
Example: 'xml,svg,xsd'

## Example usage

```yml
name: test-xmllint-format

on: [push]

jobs:
  xmllint:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v2
    - uses: lsst-ts/xmllint-format-lint-action@v0.1
      with:
        source: '.'
        exclude: './third_party ./external'
        extensions: 'xml,xsd'
```
