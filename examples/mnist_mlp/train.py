"""
Simple MLP training on MNIST using PyTorch.

This script demonstrates how to structure ML training code that can be
configured using pfig. The key is to make everything configurable through
constructor arguments and use dependency injection.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import datasets, transforms


def create_optimizer(optimizer_class, model, **kwargs):
    """Helper function to create optimizers with model parameters."""
    return optimizer_class(model.parameters(), **kwargs)


class MLP(nn.Module):
    """Simple Multi-Layer Perceptron for MNIST classification."""

    def __init__(self, input_size=784, hidden_sizes=[512, 256], num_classes=10, dropout=0.2):
        super().__init__()

        # Build layers dynamically
        layers = []
        prev_size = input_size

        for hidden_size in hidden_sizes:
            layers.extend([nn.Linear(prev_size, hidden_size), nn.ReLU(), nn.Dropout(dropout)])
            prev_size = hidden_size

        # Output layer (no dropout/activation)
        layers.append(nn.Linear(prev_size, num_classes))

        self.network = nn.Sequential(*layers)

    def forward(self, x):
        # Flatten input: (batch_size, 28, 28) -> (batch_size, 784)
        x = x.view(x.size(0), -1)
        return self.network(x)


class Trainer:
    """Simple trainer for MNIST classification."""

    def __init__(self, model, optimizer, train_loader, val_loader, device="cpu", epochs=10):
        self.model = model
        self.optimizer = optimizer
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.device = torch.device(device)
        self.epochs = epochs

        # Move model to device
        self.model.to(self.device)

    def train_epoch(self, dataloader):
        """Train for one epoch."""
        self.model.train()
        total_loss = 0
        correct = 0
        total = 0

        for batch_idx, (data, target) in enumerate(dataloader):
            data, target = data.to(self.device), target.to(self.device)

            # Forward pass
            self.optimizer.zero_grad()
            output = self.model(data)
            loss = F.cross_entropy(output, target)

            # Backward pass
            loss.backward()
            self.optimizer.step()

            # Statistics
            total_loss += loss.item()
            pred = output.argmax(dim=1, keepdim=True)
            correct += pred.eq(target.view_as(pred)).sum().item()
            total += target.size(0)

            if batch_idx % 100 == 0:
                print(f"Batch {batch_idx}, Loss: {loss.item():.6f}")

        avg_loss = total_loss / len(dataloader)
        accuracy = 100.0 * correct / total
        return avg_loss, accuracy

    def validate(self, dataloader):
        """Validate the model."""
        self.model.eval()
        total_loss = 0
        correct = 0
        total = 0

        with torch.no_grad():
            for data, target in dataloader:
                data, target = data.to(self.device), target.to(self.device)
                output = self.model(data)
                total_loss += F.cross_entropy(output, target).item()

                pred = output.argmax(dim=1, keepdim=True)
                correct += pred.eq(target.view_as(pred)).sum().item()
                total += target.size(0)

        avg_loss = total_loss / len(dataloader)
        accuracy = 100.0 * correct / total
        return avg_loss, accuracy

    def fit(self):
        """Train the model for the specified number of epochs."""
        print(f"Starting training for {self.epochs} epochs on {self.device}")
        print(f"Model: {self.model}")
        print(f"Optimizer: {self.optimizer}")

        for epoch in range(self.epochs):
            print(f"\nEpoch {epoch + 1}/{self.epochs}")
            print("-" * 50)

            # Training
            train_loss, train_acc = self.train_epoch(self.train_loader)
            print(f"Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.2f}%")

            # Validation
            val_loss, val_acc = self.validate(self.val_loader)
            print(f"Val Loss: {val_loss:.4f}, Val Acc: {val_acc:.2f}%")

        print(f"\nTraining completed! Final validation accuracy: {val_acc:.2f}%")
        return {"final_val_accuracy": val_acc, "final_val_loss": val_loss}


if __name__ == "__main__":
    # Example of direct usage (without pfig)
    print("Running MNIST MLP training without pfig configuration...")

    # Create transforms
    preprocess = transforms.Compose(
        [transforms.ToTensor(), transforms.Normalize(mean=(0.1307,), std=(0.3081,))]
    )

    # Create datasets
    train_dataset = datasets.MNIST("./data", train=True, download=True, transform=preprocess)
    val_dataset = datasets.MNIST("./data", train=False, download=True, transform=preprocess)

    # Create dataloaders
    train_loader = DataLoader(train_dataset, batch_size=128, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_dataset, batch_size=128, shuffle=False, num_workers=4)

    # Create model and optimizer
    model = MLP(hidden_sizes=[256, 128], dropout=0.3)
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    trainer = Trainer(model, optimizer, train_loader, val_loader, epochs=3)

    # Train
    results = trainer.fit()
    print(f"Training results: {results}")
