import hashlib
import time
from tempfile import TemporaryDirectory

from abc import ABC, abstractmethod

from framework.helper.ckb_cli import *
from framework.test_node import CkbNode
from framework.rpc import RPCClient


class CkbContract(ABC):

    def deploy(self, account_private, node: CkbNode):
        pass

    def get_deploy_hash_and_index(self) -> (str, int):
        pass

    @abstractmethod
    def get_arg_and_data(self, key) -> (str, str):
        pass


def deploy_ckb_contract(
    private_key,
    contract_path,
    fee_rate=2000,
    enable_type_id=True,
    api_url="http://127.0.0.1:8114",
):
    """

    Args:
        private_key:
        contract_path:
        api_url:

    Returns: tx_hash
    :param api_url:
    :param enable_type_id:

    """
    if enable_type_id:
        tmp_tx_info_path = f"/tmp/tx-{time.time_ns().real}.json"
        tmp_deploy_toml_path = "/tmp/deploy1.toml"
        deploy_toml_str = get_deploy_toml_config(
            private_key, contract_path, enable_type_id
        )
        with open(tmp_deploy_toml_path, "w") as f:
            f.write(deploy_toml_str)
        account = util_key_info_by_private_key(private_key)
        deploy_gen_txs(
            account["address"]["testnet"],
            tmp_deploy_toml_path,
            tmp_tx_info_path,
            api_url,
        )
        deploy_sign_txs(private_key, tmp_tx_info_path, api_url)
        deploy_tx_result = deploy_apply_txs(tmp_tx_info_path, api_url)
        return deploy_tx_result["cell_tx"]

    # rand ckb address ,provider contract cell cant be used
    to_ckb_address = "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqwgx292hnvmn68xf779vmzrshpmm6epn4g6eqkaw"
    with open(contract_path, "rb") as f:
        # +100 provider capacity enough deploy contract
        capacity = len(f.read()) + 100
    cmd = (
        f"export API_URL={api_url} &&  echo {private_key} > /tmp/tmp.data && {cli_path} wallet transfer "
        f"--privkey-path /tmp/tmp.data --to-address  {to_ckb_address}  --capacity {capacity} --to-data-path {contract_path}  --fee-rate {fee_rate}"
        f" && rm /tmp/tmp.data"
    )
    return run_command(cmd).replace("\n", "")


def upgrade_ckb_type_contract(
    private_key,
    contract_path,
    contract_out_point_tx_hash,
    contract_out_point_tx_index=0,
    fee=1000,
    api_url="http://127.0.0.1:8114",
):
    """Replace a live Type ID code cell and return the submitted transaction hash.

    The upgraded cell is output 0, with the original lock and type scripts.
    ``fee`` is an absolute fee in Shannon, not a fee rate. Extra capacity is
    selected from the owner's empty, untyped cells only when necessary; any
    surplus stays in the upgraded cell. The caller must wait for confirmation
    and use the returned hash/index 0 for subsequent upgrades and cell deps.
    Supports the standard secp256k1 sighash lock used by deploy_ckb_contract.
    """
    if not isinstance(fee, int) or isinstance(fee, bool) or fee < 0:
        raise ValueError("fee must be a non-negative integer in Shannon")
    index = contract_out_point_tx_index
    if isinstance(index, str):
        index = int(index, 16) if index.startswith("0x") else int(index)
    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index <= 0xFFFFFFFF
    ):
        raise ValueError("contract output index must be a uint32")
    with open(contract_path, "rb") as contract_file:
        data = contract_file.read()

    client = RPCClient(api_url)
    cell = client.get_live_cell(hex(index), contract_out_point_tx_hash, True)
    if cell["status"] != "live":
        raise ValueError("contract cell is not live")
    output = cell["cell"]["output"]
    type_script = output.get("type")
    if (
        not type_script
        or type_script["code_hash"] != "0x" + "00" * 25 + "545950455f4944"
        or type_script["hash_type"] != "type"
        or len(bytes.fromhex(type_script["args"][2:])) != 32
    ):
        raise ValueError("contract cell must have a built-in Type ID script")
    account = util_key_info_by_private_key(private_key)
    lock = output["lock"]
    if lock != {
        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
        "hash_type": "type",
        "args": account["lock_arg"],
    }:
        raise ValueError(
            "contract lock must be the private key's standard sighash lock"
        )

    # Capacity occupies 8 bytes; each script occupies 32 + 1 + len(args).
    occupied = 8 + len(data) + 66
    occupied += len(bytes.fromhex(lock["args"][2:]))
    occupied += len(bytes.fromhex(type_script["args"][2:]))
    required_capacity = occupied * 100000000 + fee
    capacity = int(output["capacity"], 16)
    inputs = [(contract_out_point_tx_hash, index)]
    if capacity < required_capacity:
        live_cells = wallet_get_live_cells(
            account["address"]["testnet"], api_url=api_url
        )
        for candidate in live_cells["live_cells"]:
            candidate_index = candidate["output_index"]
            if isinstance(candidate_index, str):
                candidate_index = (
                    int(candidate_index, 16)
                    if candidate_index.startswith("0x")
                    else int(candidate_index)
                )
            out_point = (candidate["tx_hash"], candidate_index)
            if out_point in inputs or not candidate.get("mature", True):
                continue
            funding = client.get_live_cell(
                hex(candidate_index), candidate["tx_hash"], True
            )
            if funding["status"] != "live":
                continue
            funding_output = funding["cell"]["output"]
            if (
                funding_output.get("type") is not None
                or funding_output["lock"] != lock
                or funding["cell"]["data"]["content"] != "0x"
            ):
                continue
            inputs.append(out_point)
            capacity += int(funding_output["capacity"], 16)
            if capacity >= required_capacity:
                break
    if capacity < required_capacity:
        raise ValueError("insufficient capacity for upgraded contract and fee")

    with TemporaryDirectory(prefix="ckb-upgrade-") as directory:
        tx_file = f"{directory}/tx.json"
        tx_init(tx_file, api_url)
        for tx_hash, input_index in inputs:
            # The CLI adds the standard lock dependency with the input.
            tx_add_input(tx_hash, input_index, tx_file, api_url)
        tx_add_output(
            {**output, "capacity": hex(capacity - fee)}, "0x" + data.hex(), tx_file
        )
        signatures = tx_sign_inputs(private_key, tx_file, api_url)
        if not signatures:
            raise ValueError("no signature produced for upgrade transaction")
        for signature in signatures:
            tx_add_signature(
                signature["lock-arg"], signature["signature"], tx_file, api_url
            )
        return tx_send(tx_file, api_url).strip()


def get_ckb_contract_codehash(
    tx_hash, tx_index, enable_type_id=True, api_url="http://127.0.0.1:8114"
):
    if enable_type_id:
        type_arg = (
            RPCClient(api_url)
            .get_transaction(tx_hash)["transaction"]["outputs"][tx_index]["type"][
                "args"
            ]
            .replace("0x", "")
        )
        # code_hash = "0x00000000000000000000000000000000000000000000000000545950455f4944"
        # hash_type = "type" #01
        # ckb.script.pack
        data = bytes.fromhex(
            f"5500000010000000300000003100000000000000000000000000000000000000000000000000000000545950455f49440120000000{type_arg}"
        )
    else:
        tx_data = RPCClient(api_url).get_transaction(tx_hash)["transaction"][
            "outputs_data"
        ][tx_index]
        data = bytes.fromhex(tx_data.replace("0x", ""))
    personalization = "ckb-default-hash".encode("utf-8")
    # cal blake 2b hash
    hash_object = hashlib.blake2b(digest_size=32, person=personalization)
    hash_object.update(data)
    hex_digest = hash_object.hexdigest()
    return f"0x{hex_digest}"


def invoke_ckb_contract(
    account_private,
    contract_out_point_tx_hash,
    contract_out_point_tx_index,
    type_script_arg,
    hash_type="type",
    data="0x",
    fee=1000,
    api_url="http://127.0.0.1:8114",
    cell_deps=[],
    input_cells=[],
    output_lock_arg="0x470dcdc5e44064909650113a274b3b36aecb6dc7",
    skip_type_cells=False,
):
    """

    Args:
        account_private:
        contract_out_point_tx_hash:
        contract_out_point_tx_index:
        type_script_arg:
        hash_type:  data ,data1,type data2
        data:
        fee:
        api_url:
        skip_type_cells: Exclude live cells with a type script from automatic
            capacity input selection. Explicit ``input_cells`` are unchanged.

    Returns:

    """
    if hash_type == "type":
        contract_code_hash = get_ckb_contract_codehash(
            contract_out_point_tx_hash,
            contract_out_point_tx_index,
            enable_type_id=True,
            api_url=api_url,
        )
    else:
        contract_code_hash = get_ckb_contract_codehash(
            contract_out_point_tx_hash,
            contract_out_point_tx_index,
            enable_type_id=False,
            api_url=api_url,
        )
    # get input_cell
    account = util_key_info_by_private_key(account_private)
    account_address = account["address"]["testnet"]
    account_live_cells = wallet_get_live_cells(account_address, api_url=api_url)
    input_cell_lock = account["lock_arg"]
    assert len(account_live_cells["live_cells"]) > 0
    input_cell_out_points = []
    input_cell_cap = 0
    for i in range(len(input_cells)):
        input_cell_out_points.append(input_cells[i])
        cell = RPCClient(api_url).get_live_cell(
            hex(input_cells[i]["index"]), input_cells[i]["tx_hash"], True
        )
        input_cell_cap += int(cell["cell"]["output"]["capacity"], 16)
    input_cells_hashes = [input_cell["tx_hash"] for input_cell in input_cells]

    for i in range(len(account_live_cells["live_cells"])):
        live_cell = account_live_cells["live_cells"][i]
        if live_cell["tx_hash"] in input_cells_hashes:
            continue
        if skip_type_cells and live_cell.get("type_hashes") is not None:
            continue
        if input_cell_cap > 20000000000:
            break
        input_cell_out_point = {
            "tx_hash": live_cell["tx_hash"],
            "index": live_cell["output_index"],
        }

        input_cell_cap += (
            float(live_cell["capacity"].replace("(CKB)", "").strip()) * 100000000
        )

        input_cell_out_points.append(input_cell_out_point)
        if input_cell_cap > 20000000000:
            break

    # get output_cells.cap = input_cell.cap - fee
    #  "capacity": "21685.0 (CKB)",
    output_cell_capacity = input_cell_cap - fee
    output_cells = []
    if output_cell_capacity > 1000 * 100000000:
        print("output_cell_capacity to big ")
        output_cell = {
            "capacity": hex(int(500 * 100000000)),
            # rand ckb address for pass ckb-cli check lock address
            "lock": {
                "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                "hash_type": "type",
                "args": output_lock_arg,
            },
            "type": {
                "code_hash": contract_code_hash,
                "hash_type": hash_type,
                "args": type_script_arg,
            },
        }
        output_cells.append(output_cell)
        output_cells.append(
            {
                "capacity": hex(int(output_cell_capacity - 500 * 100000000)),
                # rand ckb address for pass ckb-cli check lock address
                "lock": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": input_cell_lock,
                },
            }
        )
    else:
        output_cell = {
            "capacity": hex(int(output_cell_capacity)),
            # rand ckb address for pass ckb-cli check lock address
            "lock": {
                "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                "hash_type": "type",
                "args": output_lock_arg,
            },
            "type": {
                "code_hash": contract_code_hash,
                "hash_type": hash_type,
                "args": type_script_arg,
            },
        }
        output_cells.append(output_cell)
    # add dep
    cell_dep = {
        "tx_hash": contract_out_point_tx_hash,
        "index": hex(contract_out_point_tx_index),
    }
    # tx file init
    tmp_tx_file = "/tmp/demo.json"
    tx_init(tmp_tx_file, api_url)
    tx_add_multisig_config(account_address, tmp_tx_file, api_url)
    # add input
    heads = set()
    for i in range(len(input_cell_out_points)):
        input_cell_out_point = input_cell_out_points[i]
        tx_add_input(
            input_cell_out_point["tx_hash"],
            input_cell_out_point["index"],
            tmp_tx_file,
            api_url,
        )
        transaction = RPCClient(api_url).get_transaction(
            input_cell_out_point["tx_hash"]
        )
        heads.add(transaction["tx_status"]["block_hash"])
    transaction = RPCClient(api_url).get_transaction(contract_out_point_tx_hash)
    heads.add(transaction["tx_status"]["block_hash"])
    for head in heads:
        print("add header:", head)
        tx_add_header_dep(head, tmp_tx_file)
    # add output
    for output_cell in output_cells:
        tx_add_output(
            output_cell,
            data,
            tmp_tx_file,
        )
        data = "0x"
    # add dep
    for cell_dep_tmp in cell_deps:
        tx_add_cell_dep(cell_dep_tmp["tx_hash"], cell_dep_tmp["index"], tmp_tx_file)
    tx_add_cell_dep(cell_dep["tx_hash"], cell_dep["index"], tmp_tx_file)

    # sign
    sign_data = tx_sign_inputs(account_private, tmp_tx_file, api_url)
    tx_add_signature(
        sign_data[0]["lock-arg"], sign_data[0]["signature"], tmp_tx_file, api_url
    )
    tx_info(tmp_tx_file, api_url)
    for i in range(len(input_cells)):
        cell = RPCClient(api_url).get_live_cell(
            hex(input_cells[i]["index"]), input_cells[i]["tx_hash"], True
        )

    # send tx return hash
    return tx_send(tmp_tx_file, api_url).strip()


def build_invoke_ckb_contract(
    account_private,
    contract_out_point_tx_hash,
    contract_out_point_tx_index,
    type_script_arg,
    hash_type="type",
    data="0x",
    fee=1000,
    api_url="http://127.0.0.1:8114",
):
    """

    Args:
        account_private:
        contract_out_point_tx_hash:
        contract_out_point_tx_index:
        type_script_arg:
        hash_type:  data ,data1,type data2
        data:
        fee:
        api_url:

    Returns:

    """
    if hash_type == "type":
        contract_code_hash = get_ckb_contract_codehash(
            contract_out_point_tx_hash,
            contract_out_point_tx_index,
            enable_type_id=True,
            api_url=api_url,
        )
    else:
        contract_code_hash = get_ckb_contract_codehash(
            contract_out_point_tx_hash,
            contract_out_point_tx_index,
            enable_type_id=False,
            api_url=api_url,
        )
    # get input_cell
    account = util_key_info_by_private_key(account_private)
    account_address = account["address"]["testnet"]
    account_live_cells = wallet_get_live_cells(account_address, api_url=api_url)
    assert len(account_live_cells["live_cells"]) > 0
    input_cell_out_points = []
    input_cell_cap = 0
    for i in range(len(account_live_cells["live_cells"])):
        input_cell_out_point = {
            "tx_hash": account_live_cells["live_cells"][i]["tx_hash"],
            "index": account_live_cells["live_cells"][i]["output_index"],
        }
        input_cell_cap += (
            float(
                account_live_cells["live_cells"][i]["capacity"]
                .replace("(CKB)", "")
                .strip()
            )
            * 100000000
        )
        input_cell_out_points.append(input_cell_out_point)
        if input_cell_cap > 10000000000:
            break

    # get output_cells.cap = input_cell.cap - fee
    #  "capacity": "21685.0 (CKB)",
    output_cell_capacity = input_cell_cap - fee

    output_cell = {
        "capacity": hex(int(output_cell_capacity)),
        # rand ckb address for pass ckb-cli check lock address
        "lock": {
            "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
            "hash_type": "type",
            "args": "0x470dcdc5e44064909650113a274b3b36aecb6dc7",
        },
        "type": {
            "code_hash": contract_code_hash,
            "hash_type": hash_type,
            "args": type_script_arg,
        },
    }
    # add dep
    cell_dep = {
        "tx_hash": contract_out_point_tx_hash,
        "index": hex(contract_out_point_tx_index),
    }
    # tx file init
    tmp_tx_file = "/tmp/demo.json"
    tx_init(tmp_tx_file, api_url)
    tx_add_multisig_config(account_address, tmp_tx_file, api_url)
    # add input
    for i in range(len(input_cell_out_points)):
        input_cell_out_point = input_cell_out_points[i]
        tx_add_input(
            input_cell_out_point["tx_hash"],
            input_cell_out_point["index"],
            tmp_tx_file,
            api_url,
        )
    transaction = RPCClient(api_url).get_transaction(contract_out_point_tx_hash)
    tx_add_header_dep(transaction["tx_status"]["block_hash"], tmp_tx_file)
    # add output
    tx_add_type_out_put(
        output_cell["type"]["code_hash"],
        output_cell["type"]["hash_type"],
        output_cell["type"]["args"],
        output_cell["capacity"],
        data,
        tmp_tx_file,
    )
    # add dep
    tx_add_cell_dep(cell_dep["tx_hash"], cell_dep["index"], tmp_tx_file)
    # sign
    sign_data = tx_sign_inputs(account_private, tmp_tx_file, api_url)
    tx_add_signature(
        sign_data[0]["lock-arg"], sign_data[0]["signature"], tmp_tx_file, api_url
    )
    tx_info(tmp_tx_file, api_url)
    # send tx return hash
    return build_tx_info(tmp_tx_file)


def build_tx_info(tmp_tx_file):
    with open(tmp_tx_file, "r") as file:
        tx_info_str = file.read()
    tx = json.loads(tx_info_str)
    sign_keys = list(tx["signatures"].keys())[0]
    witness = (
        "0x5500000010000000550000005500000041000000"
        + tx["signatures"][sign_keys][0][2:]
    )
    tx_msg = tx["transaction"]
    tx_msg["witnesses"] = [witness]
    return tx_msg
