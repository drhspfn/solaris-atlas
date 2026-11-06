import base64
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "npc_research_shops", Path(__file__).resolve().parents[1] / "npc_research_shops.py")
shops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shops)


class ShopEvidenceTests(unittest.TestCase):
    def test_iris_authored_rows_decode_prices_vectors_and_defaults(self):
        # Real local db_shop.db blobs, independently checked with client JS readers.
        info = base64.b64decode(
            "GAAAAAAAEgAcABgAFAAQAAAADAAIAAQAEgAAABgAAAAgAAAAJAAAACgAAAABAAAAjAAAAAQAAAAxMTIzAAAAAAAAAAAAAAAAAQAAAAIAAAAVAAAAU2hvcEluZm9fMTQwX1Nob3BOYW1lAAAA")
        fixed = base64.b64decode(
            "KAAAAAAAIgAoACQAIAAAABwAGAAAAAAAAAAUAAAAEAAAAAwACAAEACIAAAAkAAAAKAAAACwAAAAwAAAAAQAAACgAAAADAAAAjAAAALE2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAADAAAAAgADAAIAAQACAAAAJABAABCAAAA")
        self.assertEqual(shops.decode(info), {
            "Id": 140, "ShopType": 1, "ShopName": "ShopInfo_140_ShopName", "OpenId": 0,
            "ShowCurrency": [2], "DialogueText": "", "UiCamera": "1123"})
        row = shops.decode(fixed, fixed=True)
        self.assertEqual((row["Id"], row["ShopId"], row["ItemId"], row["ItemNum"], row["LimitNum"]),
                         (14001, 140, 3, 40, 1))
        self.assertEqual(row["Price"], {"66": 400})
        self.assertEqual(row["OriginalPrice"], {})
        self.assertTrue(row["Show"])
        # Empty table uses JS defaults, including nonzero ItemNum/Show and -1 limit.
        empty = shops.decode(bytes.fromhex("080000000400040004000000"), fixed=True)
        self.assertEqual((empty["ItemNum"], empty["LimitNum"], empty["Show"]), (1, -1, True))

    def test_only_shop_actions_establish_ownership_and_flow_context_is_retained(self):
        action = {"Name": "OpenSystemBoard", "Params": {"SystemType": "Shop", "BoardId": 140}}
        flow = {"FlowListName": "npc", "FlowId": 1, "StateId": 1}
        row = {"Id": 9, "MapId": 906, "EntityId": 152700001, "ComponentsData": {
            "InteractComponent": {"PreFlow": flow, "Options": [{"Condition": {"Type": "Night"},
                "Type": {"Actions": [{"Params": {"Then": [action]}}]}}]},
            "NpcPerformComponent": {"ShowOnUiInteract": {"ShopSuccessFlow": {"StateId": 3}}}}}
        result = next(shops.entity_shops([row]))
        self.assertEqual(result["shop_actions"][0]["action"], action)
        self.assertIn("/Options/0/Type/Actions/0/Params/Then/0", result["shop_actions"][0]["pointer"])
        self.assertEqual(result["flows"][-1]["resolved"], {**flow, "StateId": 3})
        self.assertEqual(result["Options"][0]["Condition"], {"Type": "Night"})
        action["Params"]["SystemType"] = "PayShop"
        self.assertEqual(list(shops.entity_shops([row])), [])


if __name__ == "__main__":
    unittest.main()
