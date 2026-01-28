#!/bin/bash -ex

RCLONE_VER=1.72.1

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

mkdir -p ~/.local/bin
export PATH=${HOME}/.local/bin:${PATH}
for bin in ${SCRIPT_DIR}/bin/*; do
  cp -a ${bin} ~/.local/bin
done

cp ${SCRIPT_DIR}/conf/.model-index.json ~

sudo apt-get update && sudo apt-get install -y unzip nload

pushd /tmp
curl -LO https://downloads.rclone.org/v${RCLONE_VER}/rclone-v${RCLONE_VER}-linux-amd64.zip
unzip rclone-v${RCLONE_VER}-linux-amd64.zip
mv rclone-v${RCLONE_VER}-linux-amd64/rclone ~/.local/bin
popd

mkdir ~/gdrive
rclone mount --dir-cache-time 30s --vfs-links --vfs-cache-mode full --vfs-cache-max-size 200G --daemon gdrive: ~/gdrive

curl -LsSf https://astral.sh/uv/install.sh | sh

cd ~
git clone https://github.com/Comfy-Org/ComfyUI comfyui
cd comfyui

cat > extra_model_paths.yaml <<EOF
comfyui:
     base_path: /home/riftuser/gdrive/comfyui
     is_default: true
     checkpoints: models/checkpoints/
     text_encoders: models/text_encoders/
     clip_vision: models/clip_vision/
     configs: models/configs/
     controlnet: models/controlnet/
     diffusion_models: models/diffusion_models
     embeddings: models/embeddings/
     loras: models/loras/
     upscale_models: models/upscale_models/
     vae: models/vae/
     audio_encoders: models/audio_encoders/
     model_patches: models/model_patches/
EOF

uv venv --python 3.13 --managed-python
source .venv/bin/activate
uv pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu128
uv pip install -r requirements.txt
uv pip install -r manager_requirements.txt
uv pip install triton
uv pip install sageattention

comfy
