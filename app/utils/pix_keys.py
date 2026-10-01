"""PIX key normalization and Brazilian document validation."""
import re


def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    numbers = [int(char) for char in digits]
    first = (sum(number * weight for number, weight in zip(numbers[:9], range(10, 1, -1))) * 10) % 11
    first = 0 if first == 10 else first
    second = (sum(number * weight for number, weight in zip(numbers[:10], range(11, 1, -1))) * 10) % 11
    second = 0 if second == 10 else second
    return numbers[9] == first and numbers[10] == second


def _valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    numbers = [int(char) for char in digits]
    first_weights = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    first_sum = sum(number * weight for number, weight in zip(numbers[:12], first_weights))
    first = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    second_weights = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    second_sum = sum(number * weight for number, weight in zip(numbers[:13], second_weights))
    second = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return numbers[12] == first and numbers[13] == second


def normalize_pix_key_value(key_type: str, key_value: str) -> str:
    """Strip Brazilian document punctuation and validate its check digits."""
    kind = str(getattr(key_type, "value", key_type)).strip().upper()
    value = str(key_value or "").strip()
    if kind in {"CPF", "CNPJ"}:
        if not re.fullmatch(r"[\d.\-/\s]+", value):
            raise ValueError(f"A chave {kind} deve conter apenas números e pontuação de documento.")
        digits = re.sub(r"\D", "", value)
        valid = _valid_cpf(digits) if kind == "CPF" else _valid_cnpj(digits)
        if not valid:
            raise ValueError(f"A chave {kind} é inválida.")
        return digits
    if not value:
        raise ValueError("A chave PIX não pode ficar vazia.")
    return value
