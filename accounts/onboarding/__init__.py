"""The onboarding questionnaire, as a finite state machine.

- ``machine`` — the engine: states, events, guards, the run, its projections
  (path, current state, back target, counter) and the transition rules.
  Pure Python, no Django import, checked mechanically at import of ``flow``.
- ``flow`` — the states themselves: the screens, their options and copy, the
  guards, and the one ``machine`` instance the views use.
- ``store`` — where a run lives while it is in flight: the session.
- ``forms``, ``services``, ``views`` — the web side; ``data`` — the static
  suggestion lists; ``testing`` — fixtures shared by the test modules.
"""
