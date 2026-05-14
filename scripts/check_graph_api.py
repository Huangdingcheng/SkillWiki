import json
import requests


GRAPH_URL = "http://localhost:8000/api/v1/graph"


def main():
    try:
        response = requests.get(GRAPH_URL, timeout=10)
        print("Status code:", response.status_code)

        response.raise_for_status()
        data = response.json()

        print("Top-level keys:", list(data.keys()))
        print("Node count:", len(data.get("nodes", [])))
        print("Edge count:", len(data.get("edges", [])))

        nodes = data.get("nodes", [])
        if nodes:
            print("\nFirst node:")
            print(json.dumps(nodes[0], indent=2, ensure_ascii=False))

        with open("test_outputs/graph-current.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print("\nSaved to test_outputs/graph-current.json")

    except requests.exceptions.RequestException as e:
        print("Request failed:", e)


if __name__ == "__main__":
    main()