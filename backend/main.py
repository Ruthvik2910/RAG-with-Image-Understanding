"""Small CLI using the same scoped API paths as the web frontend.
Start uvicorn backend.api:api first, then python -m backend.main.
"""
from pathlib import Path
import mimetypes
import os
import httpx


def main():
    base = os.getenv("RAG_API_URL", "http://localhost:8000").rstrip("/")
    with httpx.Client(base_url=base, timeout=180) as client:
        client.get("/config").raise_for_status()
        while True:
            print("\n1. Add document  2. List documents  3. Delete document  4. Ask  5. Evaluation  6. Exit")
            choice = input("Select: ").strip()
            try:
                if choice == "6":
                    break
                if choice == "1":
                    path = Path(input("File path: ").strip().strip('"'))
                    with path.open("rb") as stream:
                        response = client.post("/upload", files={"files": (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})
                elif choice == "2":
                    response = client.get("/documents")
                elif choice == "3":
                    response = client.delete("/documents/" + input("Document ID: ").strip())
                elif choice == "4":
                    response = client.post("/chat", json={"question": input("Question: ").strip()})
                    response.raise_for_status()
                    result = response.json()
                    if result.get("status") == "approval_required":
                        approved = input("Approve configured web search? [y/N]: ").lower() == "y"
                        response = client.post("/chat/approval", json={"thread_id": result["thread_id"], "approved": approved})
                elif choice == "5":
                    response = client.get("/evaluation")
                else:
                    continue
                if not response.is_success:
                    print(response.json().get("detail", "Request failed."))
                else:
                    result = response.json()
                    print(result.get("answer", result))
            except (OSError, httpx.HTTPError) as exc:
                print(f"Unable to complete request: {exc}")

if __name__ == "__main__":
    main()
