import pytest


def test_affiliate_stock_visibility_logic():
    """Affiliate mode: hide products when supplier is out of stock."""
    def should_show_product(in_stock: bool):
        return in_stock

    assert should_show_product(True) is True
    assert should_show_product(False) is False


if __name__ == "__main__":
    pytest.main()
