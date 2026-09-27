# Making pytest import the project modules (api_client, deleter, …) when run
# from any working directory: a root-level conftest.py puts the repo root on
# sys.path under pytest's default "prepend" import mode.
