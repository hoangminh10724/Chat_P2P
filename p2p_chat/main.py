import argparse
import socket

from p2p_chat import client, server, spec


parser = argparse.ArgumentParser(prog="p2p_demo", description="True P2P Chat System")
subparser = parser.add_subparsers(dest="sub_func")

parser_run = subparser.add_parser("run", help="Run tracker, console peer, or GUI peer")
parser_run.add_argument("module", help="Module can chay.", choices=["server", "client", "gui"])
parser_run.add_argument("--host", default=spec.HOST, help="Host tracker bind vao khi chay server")
parser_run.add_argument("--port", type=int, default=spec.PORT, help="Cong tracker")
parser_run.add_argument("--server-host", default=None, help="Host tracker de peer ket noi")
parser_run.add_argument("--server-port", type=int, default=None, help="Cong tracker de peer ket noi")
parser_run.add_argument("--username", default=None, help="Ten peer khi chay console client")
parser_run.add_argument("--base-port", type=int, default=spec.P2P_BASE_PORT, help="Cong bat dau cho PeerServer")

ARGS = parser.parse_known_args()[0]
KWARGS = vars(ARGS)


def _valid_username(username: str) -> bool:
    return bool(username) and not any(c.isspace() for c in username)


def main():
    module = KWARGS.get("module")

    if module == "server":
        bind_address = (KWARGS["host"], KWARGS["port"])
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(bind_address)
        s.listen(50)
        print("=" * 55)
        print("  BOOTSTRAP SERVER - He thong phan tan P2P Chat")
        print("=" * 55)
        print(f"  Bind   : {bind_address[0]}:{bind_address[1]}")
        if bind_address[0] in ("0.0.0.0", ""):
            print(f"  LAN IP : {spec.get_lan_ip()}:{bind_address[1]}")
        print("  [START] Server dang lang nghe...")
        print("=" * 55)
        server.receive(s)
        return

    if module == "client":
        username = KWARGS.get("username") or input("  [PROMPT] Nhap ten nguoi dung: ").strip()
        if not _valid_username(username):
            print("[ERROR] Ten nguoi dung khong duoc rong hoac chua khoang trang.")
            return

        server_host = KWARGS.get("server_host") or KWARGS.get("host") or spec.HOST
        server_port = KWARGS.get("server_port") or KWARGS.get("port") or spec.PORT
        server_address = (server_host, server_port)

        print("=" * 55)
        print("  PEER NODE - He thong phan tan P2P Chat")
        print("=" * 55)
        print(f"  Username : {username}")
        print(f"  Tracker  : {server_address[0]}:{server_address[1]}")
        print("=" * 55)
        client.run_client(
            server_address=server_address,
            username=username,
            base_port=KWARGS["base_port"],
        )
        return

    if module == "gui":
        from p2p_chat.gui import launch

        launch()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
