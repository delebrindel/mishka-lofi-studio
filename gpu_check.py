"""Diagnóstico rápido: ¿PyTorch / Demucs ven la tarjeta NVIDIA?   python gpu_check.py"""
import shutil, subprocess, sys, time

print("Python:", sys.version.split()[0], "|", sys.executable)
try:
    import torch
except ImportError:
    sys.exit("✗ PyTorch no está instalado en este entorno. ¿Activaste .venv?")
print("PyTorch:", torch.__version__, "| CUDA en el build:", torch.version.cuda)
try:
    import torchaudio
    print("torchaudio:", torchaudio.__version__)
    if torchaudio.__version__.split("+")[0] != torch.__version__.split("+")[0]:
        print("⚠ torch y torchaudio no son la misma versión")
except Exception as e:
    print("✗ torchaudio no carga:", str(e).splitlines()[-1][:150])
    print("  (casi siempre: torch y torchaudio de versiones distintas)")

if shutil.which("nvidia-smi"):
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    print("nvidia-smi:", out or "(sin salida)")
else:
    print("nvidia-smi: no encontrado (¿driver de NVIDIA instalado?)")

if not torch.cuda.is_available():
    if torch.version.cuda is None or "+cpu" in torch.__version__:
        print("\n✗ Tienes PyTorch versión CPU. Arreglo:")
        print("  pip uninstall -y torch torchaudio")
        print("  pip install -r requirements.txt")
    else:
        print("\n✗ PyTorch tiene CUDA pero no ve la tarjeta: actualiza el driver de NVIDIA y reinicia.")
    sys.exit(1)

print("\n✓ GPU:", torch.cuda.get_device_name(0))
x = torch.randn(4096, 4096, device="cuda")
torch.cuda.synchronize(); t = time.time()
for _ in range(20):
    x = x @ x
    x = x / x.norm()
torch.cuda.synchronize()
print(f"✓ prueba de cálculo en GPU: {time.time() - t:.2f} s")
