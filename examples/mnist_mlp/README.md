# MNIST MLP with pfig

Train a PyTorch MLP on MNIST using pfig configuration. This example demonstrates pfig's **include**, **override**, and **external** systems for modular, environment-aware config composition.

## Files

- `train.py` - PyTorch training code
- `main.pfig` - Main pfig configuration with override example
- `consts.pfig` - Training constants, hyperparameters, and externals
- `profiles/` - Runtime environment profiles
  - `gpu.pfig` - Default GPU configuration
  - `cpu.pfig` - CPU-only configuration
- `README.md` - This file

## Quick Start

```bash
# Install dependencies
pip install torch torchvision

# Run with gpu profile
cd examples/mnist_mlp
pfig run main.pfig --profile profiles/gpu.pfig

# Run on CPU only
pfig run main.pfig --profile profiles/cpu.pfig

# Run with custom external values
pfig run main.pfig --set device=cpu --set data_dir=/tmp/mnist --set num_workers=2
```

## Configuration Architecture

This example demonstrates a **3-layer configuration system**:

### 1. Core Configuration (`consts.pfig`)
Algorithm parameters and external declarations:
```pfig
# Training hyperparameters (algorithm configuration)
epochs = 5
batch_size = 128
learning_rate = 0.001

# External parameters (runtime environment)
external device       # "cpu", "cuda", etc.
external data_dir     # Data storage location
external num_workers  # Number of data loading workers

# Model architecture
input_size = 784
hidden_sizes = [512, 256, 128]
num_classes = 10
dropout = 0.2
```

### 2. Main Logic (`main.pfig`)
Wiring components together with overrides:
```pfig
include "./consts.pfig" as consts

# Override algorithmic parameter
override consts/epochs = 10

# Components use both regular constants and externals
model = MLP(hidden_sizes=@consts/hidden_sizes, dropout=@consts/dropout)
trainer = Trainer(
    model=@model,
    device=@consts/device,      # External value
    epochs=@consts/epochs       # Overridden value
)

__entrypoint__ = @trainer.fit
```

### 3. Environment Profiles (`profiles/*.pfig`)
Runtime environment configuration:
```pfig
# profiles/gpu.pfig - just overrides, no external redeclaration needed!
override device = "cuda"
override data_dir = "./data"
override num_workers = 8
```

## Environment Profiles

Three pre-configured profiles are provided:

### `profiles/gpu.pfig` - GPU Training
```bash
pfig run main.pfig --profile profiles/gpu.pfig
```
- Uses GPU acceleration (`device = "cuda"`)
- Local data directory (`data_dir = "./data"`)
- 8 data loading workers

### `profiles/cpu.pfig` - CPU-Only Training
```bash
pfig run main.pfig --profile profiles/cpu.pfig
```
- Forces CPU usage (`device = "cpu"`)
- Fewer workers for reduced memory pressure (`num_workers = 4`)

## Customization Options

### 1. **Algorithm Changes** - Edit `consts.pfig`:
```pfig
# Experiment with hyperparameters
learning_rate = 0.01
hidden_sizes = [1024, 512, 256]
batch_size = 256
```

### 2. **Runtime Environment** - Use CLI flags:
```bash
# Custom device and data location
pfig run main.pfig --set device=cpu --set data_dir=/fast/ssd/mnist

# Override multiple externals
pfig run main.pfig --set device=cuda --set num_workers=8 --set data_dir=/tmp
```

### 3. **Create Custom Profiles**:
```pfig
# profiles/my_setup.pfig - clean and simple!
override device = "cuda:1"        # Use specific GPU
override data_dir = "/my/data"    # Custom data location
override num_workers = 16         # High-performance setup
```

## Key Benefits

- **🔧 Algorithm vs Environment**: Cleanly separates ML hyperparameters from runtime configuration
- **📁 Profile Management**: Easy switching between development, testing, and production environments
- **📝 Simple Profiles**: No need to redeclare externals in profiles - just provide overrides!
- **⚡ CLI Flexibility**: Quick parameter tweaks without editing files
- **🔒 Type Safety**: Profiles can only override declared externals (validated at runtime)

## Expected Results

- ~97% validation accuracy (with override: 10 epochs instead of 5)
- Training time: ~2-3 minutes CPU, ~1 minute GPU
