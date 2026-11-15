# buggy-calc

Tiny repo with one known bug, used by DevSquad's tests and for a first manual try.

Bug report to give DevSquad:

> `average([])` crashes with `ZeroDivisionError`. The docstring says an empty
> list should average to 0.0.

Test command: `python -m unittest discover -s tests -t .`
