# Fashion-MNIST dataset

Official Fashion-MNIST by Zalando Research: https://github.com/zalandoresearch/fashion-mnist

60,000 training and 10,000 test images, 28×28 grayscale, with labels for ten clothing classes. The original IDX files are in `FashionMNIST/raw`, ready for offline `torchvision.datasets.FashionMNIST(root="examples/fashion-mnist/data", train=True, download=False)` (use `train=False` for the test set).

Classes: T-shirt/top, Trouser, Pullover, Dress, Coat, Sandal, Shirt, Sneaker, Bag, Ankle boot.

Downloaded from the official repository and verified against torchvision's upstream MD5 checksums. `manifest.json` records the uncompressed files' SHA-256 hashes and sizes. See `LICENSE` for the dataset's MIT license. This dataset contains no training results or distributed-training implementation.
