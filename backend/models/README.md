# Model weights

Model weights are intentionally not stored in this repository. By default, the API downloads the configured checkpoint from Hugging Face on the first analysis request. Set `MODEL_OFFLINE=true` only when an authorized checkpoint is available at `MODEL_WEIGHTS_PATH` or in the local Hugging Face cache.