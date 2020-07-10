#!/bin/sh -l

cd "$GITHUB_WORKSPACE"

/run-xmllint-format.py $*
