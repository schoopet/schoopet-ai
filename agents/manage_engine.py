import argparse
import os
import sys
import httpx
import google.auth
import google.auth.transport.requests

def get_access_token():
    credentials, project = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

def list_engines(project_id, location, api_version):
    print(f"Listing Reasoning Engines in project '{project_id}', location '{location}', API '{api_version}'...")
    token = get_access_token()
    url = f"https://{location}-aiplatform.googleapis.com/{api_version}/projects/{project_id}/locations/{location}/reasoningEngines"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            engines = data.get("reasoningEngines", [])
            if not engines:
                print("No engines found.")
                return
            
            print(f"{'ID':<25} | {'Display Name':<30} | {'Effective Identity'}")
            print("-" * 110)
            for engine in engines:
                full_name = engine.get("name", "N/A")
                engine_id = full_name.split('/')[-1]
                display_name = engine.get("displayName", "")
                # effectiveIdentity is nested under spec in the wire JSON
                spec = engine.get("spec", {})
                effective_identity = spec.get("effectiveIdentity", "N/A")
                
                print(f"{engine_id:<25} | {display_name:<30} | {effective_identity}")
    except Exception as e:
        print(f"Error listing engines: {e}")

def get_engine(engine_id, project_id, location, api_version):
    print(f"Getting Reasoning Engine '{engine_id}' via API '{api_version}'...")
    token = get_access_token()
    url = f"https://{location}-aiplatform.googleapis.com/{api_version}/projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            engine = response.json()
            
            print(f"\n--- Engine Details ---")
            print(f"ID:           {engine_id}")
            print(f"Name:         {engine.get('name')}")
            print(f"Display Name: {engine.get('displayName')}")
            print(f"Description:  {engine.get('description', 'N/A')}")
            
            spec = engine.get("spec", {})
            print(f"Identity Type: {spec.get('identityType', 'N/A')}")
            print(f"Eff. Identity: {spec.get('effectiveIdentity', 'N/A')}")
            print(f"Create Time:   {engine.get('createTime')}")
            print(f"Update Time:   {engine.get('updateTime')}")
            
            class_methods = spec.get("classMethods", [])
            print(f"Operations:    {len(class_methods)} defined")
            
    except Exception as e:
        print(f"Error retrieving engine: {e}")

def main():
    parser = argparse.ArgumentParser(description="Manage Vertex AI Reasoning Engines")
    subparsers = parser.add_subparsers(dest="command", required=True)

    default_project = os.getenv("GOOGLE_CLOUD_PROJECT")
    default_location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    list_parser = subparsers.add_parser("list", help="List all agent engine instances")
    list_parser.add_argument("--project", default=default_project, help="GCP Project ID")
    list_parser.add_argument("--location", default=default_location, help="GCP Location")
    list_parser.add_argument("--api-version", default="v1beta1", help="API Version (v1 or v1beta1)")

    get_parser = subparsers.add_parser("get", help="Get information about a specific agent engine instance")
    get_parser.add_argument("id", help="Agent Engine ID")
    get_parser.add_argument("--project", default=default_project, help="GCP Project ID")
    get_parser.add_argument("--location", default=default_location, help="GCP Location")
    get_parser.add_argument("--api-version", default="v1beta1", help="API Version (v1 or v1beta1)")

    args = parser.parse_args()

    if not args.project:
        print("Error: Project ID must be provided via --project or GOOGLE_CLOUD_PROJECT env var.")
        sys.exit(1)

    if args.command == "list":
        list_engines(args.project, args.location, args.api_version)
    elif args.command == "get":
        get_engine(args.id, args.project, args.location, args.api_version)

if __name__ == "__main__":
    main()