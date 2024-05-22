
import requests
import json

from dotenv import load_dotenv
from os import getenv

# Ignore ssl warnings for self-signed certificates
requests.packages.urllib3.disable_warnings()


class NXOSDevice():
    def __init__(self, ip_addr="", user="", password=""):
        self.ip_addr = ip_addr
        self.username = user
        self.password = password
        self.scheme = "https"
        self.verify_ssl = False
        self.session = requests.Session()
        self.connect()

    def _aaa_login(self):
        payload = {
            'aaaUser': {
                'attributes': {
                    'name': self.username,
                    'pwd': self.password
                }
            }
        }
        url = self._build_url("/api/aaaLogin.json")
        response = self._request(
            "POST", url, data=json.dumps(payload))
        if response.status_code == requests.codes.ok:
            print("aaaLogin RESPONSE:")
            print(json.dumps(json.loads(response.text), indent=2))
        return response.status_code

    def _aaa_logout(self):
        payload = {
            'aaaUser': {
                'attributes': {
                    'name': self.username
                }
            }
        }
        url = self._build_url("/api/aaaLogout.json")

        response = self._request(
            "POST", url, data=json.dumps(payload))
        print()
        print("aaaLogout RESPONSE:")
        print(json.dumps(json.loads(response.text), indent=2))

    def _build_url(self, endpoint):
        return self.scheme + "://" + self.ip_addr + endpoint

    def _request(self, *args, **kwargs):
        return self.session.request(*args, verify=self.verify_ssl, **kwargs)

    def get(self, endpoint):
        url = self._build_url(endpoint)
        response = self._request(
            "GET", url)
        print()
        print("GET RESPONSE:")
        print(json.dumps(json.loads(response.text), indent=2))

    def connect(self):
        self._aaa_login()

    def disconnect(self):
        self._aaa_logout()

    def reset(self):
        pass


if __name__ == "__main__":
    load_dotenv()
    NXOS_USERNAME = getenv("NXOS_USERNAME")
    NXOS_PASSWORD = getenv("NXOS_PASSWORD")
    NXOS_HOST = getenv("NXOS_HOST")

    device = NXOSDevice(user=NXOS_USERNAME,
                        password=NXOS_PASSWORD, ip_addr=NXOS_HOST)
    device.get("/api/mo/sys.json")
