from __future__ import annotations


class DirectOrderSubmissionBlocked(RuntimeError):
    pass


def interpret_fable5_recipe(recipe_text: str) -> dict[str, object]:
    return {
        "source": "fable5_recipe",
        "recipe_text": recipe_text,
        "can_place_orders": False,
        "can_approve_orders": False,
    }


def submit_order_from_fable5_recipe(recipe_text: str) -> None:
    raise DirectOrderSubmissionBlocked("Fable5 recipes cannot directly submit or approve orders")
