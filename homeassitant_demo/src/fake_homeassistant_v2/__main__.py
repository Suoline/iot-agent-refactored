from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("fake_homeassistant_v2.app:create_app", host="127.0.0.1", port=8123, reload=False, factory=True)


if __name__ == "__main__":
    main()
