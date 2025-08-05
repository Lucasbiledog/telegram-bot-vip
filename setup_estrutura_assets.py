import os
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = "grupo free"
EXEMPLOS = ["asset1", "asset2", "asset3"]

def criar_estrutura():
    os.makedirs(BASE_DIR, exist_ok=True)

    for nome in EXEMPLOS:
        asset_dir = os.path.join(BASE_DIR, nome)
        preview_dir = os.path.join(asset_dir, "preview")
        os.makedirs(preview_dir, exist_ok=True)

        # Criar imagem de preview fake
        for i in range(1, 3):
            img_path = os.path.join(preview_dir, f"preview_{i}.jpg")
            img = Image.new("RGB", (512, 256), color=(200, 200, 200))
            d = ImageDraw.Draw(img)
            d.text((10, 120), f"Preview {i} - {nome}", fill=(0, 0, 0))
            img.save(img_path)

        # Criar arquivo fake para download
        with open(os.path.join(asset_dir, f"{nome}.txt"), "w") as f:
            f.write(f"Conteúdo do {nome}")

    print(f"Estrutura criada dentro da pasta: {BASE_DIR}")

if __name__ == "__main__":
    criar_estrutura()
