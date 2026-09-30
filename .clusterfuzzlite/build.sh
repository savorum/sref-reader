#!/bin/bash -eu

pip3 install --require-hashes -r .clusterfuzzlite/requirements.txt
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

for fuzzer in fuzz/fuzz_*.py; do
  compile_python_fuzzer "$fuzzer" --collect-data sref_reader
done
zip -qj "$OUT/fuzz_reader_seed_corpus.zip" fuzz/seeds/*
