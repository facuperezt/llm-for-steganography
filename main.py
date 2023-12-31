import json
from src.models import DynamicPOE

if __name__ == "__main__":
    dpoe = DynamicPOE(bits_per_token= 2, skip_tokens= 5, disable_tqdm=False, n_gpu_layers=10)
    with open("example_feed.json", "r") as f:
        message, feed = json.load(f).values()

    message = message[:1]
    doctored_feed = dpoe.hide(message, feed, True, 86)
    # with open("last_run_doctored_feed.json", "w") as f:
    #     json.dump(doctored_feed, f)
    # with open("last_run_doctored_feed.json", "r") as f:
    #     doctored_feed = json.load(f)

    decoded_msg = dpoe.recover(doctored_feed["feed"])
    print(message)
    print(decoded_msg["secret"])