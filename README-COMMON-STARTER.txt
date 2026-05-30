WF Common starter package

Copy the Common folder into:

    E:\OneDrive\WF\Common

Also copy .env.example into:

    E:\OneDrive\WF\.env.example

Then create your real local .env:

    E:\OneDrive\WF\.env

Desktop/server example:

    DB_MANAGER_URL=http://localhost:8787
    DICTAPI_URL=http://localhost:8788
    PROCMAN_URL=http://localhost:8790

Laptop/worker example:

    DB_MANAGER_URL=http://Office:8787
    DICTAPI_URL=http://localhost:8788
    PROCMAN_URL=http://localhost:8790

In scripts under E:\OneDrive\WF\Scripts, add repo-root import support if needed:

    from pathlib import Path
    import sys

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from Common.db_client import db_post
    from Common.wf_config import print_startup_config

First health test:

    python -c "from Common.db_client import check_db_manager_health; check_db_manager_health(fatal=True)"

Example replacement:

    OLD:
        requests.post("http://localhost:8787/v1/words/list", json=payload)

    NEW:
        from Common.db_client import db_post
        result = db_post("/v1/words/list", payload)
