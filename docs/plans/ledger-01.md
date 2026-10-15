# SDD ledger — plan: /mnt/almacen/ZARA/docs/plans/2026-10-14-zara-01-nucleo-seguridad.md

Ruling (setup): proyecto no es repo git y norma del usuario = no git → sin worktree, trabajo directo en app/, "commits" = puntos de control (tests). Coste si mal: nulo (git se puede añadir después).
Ruling (setup): uv/Python 3.12 no disponible; Plan 1 solo usa stdlib+pytest → venv con Python 3.14 del sistema, código escrito compatible con 3.12. Coste si mal: rehacer venv en planes de voz; el código no cambia.
Pre-flight: interfaces encadenadas (T1 errors/ZaraPaths → T2 ConfigError/load_toml → T3 AuditLog → T4 BackupError/backup_path → T5 Level/Action/Decision → T6 RuleSet(usa Level,Action,load_toml) → T7 Guardian(usa todo)). Firmas revisadas y consistentes: classify()->tuple, backup_path(src,copias_dir,clock), AuditLog.record(event,data). Sin conflictos.
Task 1: complete (checkpoint, tests: pytest tests/test_paths.py -> 2/2 pass; suite verde)
Task 2: complete (checkpoint, tests: pytest tests/test_config.py -> 3/3 pass; suite verde 5/5)
Task 3: complete (checkpoint, tests: pytest tests/test_audit.py -> 1/1 pass; suite verde 6/6)
Task 4: complete (checkpoint, tests: pytest tests/test_backup.py -> 3/3 pass; suite verde 9/9)
Task 5: complete (checkpoint, tests: pytest tests/guardian/test_action.py -> 2/2 pass; suite verde 11/11)
Task 6: Ruling: classify() del plan tenía bug (escritura en espacio caía en fail-safe CONFIRM). Reescrito con acumulación de candidatos; fallback CONFIRM solo si no hay candidatos. Coste si mal: ninguno, más correcto.
Task 6: complete (checkpoint, tests: pytest tests/guardian/test_rules.py -> 7/7 pass; suite verde 18/18)
Task 7: Ruling: patron blocked 'rm -rf /...' demasiado amplio (marcaba cualquier borrado absoluto fuera de ZARA como BLOCKED, chocando con tests T7 y diseno). Acotado a / , /*, dirs de sistema, /home, ~. Actualizado rules.py y guardian.toml. Coste si mal: ninguno.
Task 7: complete (checkpoint, tests: pytest tests/guardian/test_engine.py -> 5/5 pass; suite verde 23/23)
Final review: self-review (no subagent tool)
Final: fixed guardian.toml corrupto no caia a defaults seguros — test_corrupt_toml_falls_back_to_safe_defaults RED->GREEN, suite 24/24.
Final: Ruling: accion CRITICAL sin paths (p.ej. shell puro) tiene needs_backup=True y prepare() lanza BackupError (fail-closed). Es seguro; el consumidor debe declarar las rutas afectadas para poder preparar/ejecutar. Coste si mal: un plan posterior debe poblar paths; sin riesgo de seguridad.
Final: creado centinela /mnt/almacen/.zara_mounted para que ZaraPaths.resolve funcione en produccion.
Final review complete: 1 importante arreglado, 0 criticos, 0 minors.
Migración: venv recreado con Python 3.12.15 (uv); suite Plan 1 24/24 verde en 3.12.
