"""Categorical law equivalence tests for ComposableStream.

Each law asserts that the optimized implementation and the categorical
derivation produce identical output when materialized via ``run_to_list()``.

Laws tested:
    SLW01  Functor identity
    SLW02  Functor composition
    SLW03  Monad left identity
    SLW04  Monad right identity
    SLW05  Monad associativity
    SLW06  Monoid left identity
    SLW07  Monoid right identity
    SLW08  Monoid associativity
    SLW09  filter ≡ flat_map derivation
    SLW10  take ≡ zip_with + unfold derivation
    SLW11  drop ≡ enumerate + flat_map derivation
    SLW12  map_concat ≡ flat_map + from_iterable derivation
    SLW13  intersperse ≡ enumerate + flat_map derivation
"""

import pytest

from everything_is_an_actor.core.composable_stream import ComposableStream

pytestmark = pytest.mark.anyio

DATA = [1, 2, 3, 4, 5]


def s() -> ComposableStream[int]:
    """Helper — fresh stream from DATA (streams are single-use)."""
    return ComposableStream.from_list(DATA)


# =================================================================
#  Functor laws
# =================================================================


@pytest.mark.asyncio
async def test_slw01_functor_identity():
    """map(id) ≡ id"""
    lhs = await s().map(lambda x: x).run_to_list()
    rhs = await s().run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw02_functor_composition():
    """map(f) . map(g) ≡ map(g . f)"""
    f = lambda x: x + 1
    g = lambda x: x * 2

    lhs = await s().map(f).map(g).run_to_list()
    rhs = await s().map(lambda x: g(f(x))).run_to_list()
    assert lhs == rhs


# =================================================================
#  Monad laws
# =================================================================


@pytest.mark.asyncio
async def test_slw03_monad_left_identity():
    """pure(a) >>= f  ≡  f(a)"""
    f = lambda x: ComposableStream.of(x, x * 10)

    lhs = await ComposableStream.of(3).flat_map(f).run_to_list()
    rhs = await f(3).run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw04_monad_right_identity():
    """m >>= pure  ≡  m"""
    lhs = await s().flat_map(ComposableStream.of).run_to_list()
    rhs = await s().run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw05_monad_associativity():
    """(m >>= f) >>= g  ≡  m >>= (λx. f(x) >>= g)"""
    f = lambda x: ComposableStream.of(x, x + 1)
    g = lambda x: ComposableStream.of(x * 10)

    lhs = await s().flat_map(f).flat_map(g).run_to_list()
    rhs = await s().flat_map(lambda x: f(x).flat_map(g)).run_to_list()
    assert lhs == rhs


# =================================================================
#  Monoid laws
# =================================================================


@pytest.mark.asyncio
async def test_slw06_monoid_left_identity():
    """empty ⊕ s  ≡  s"""
    lhs = await ComposableStream.empty().concat(s()).run_to_list()
    rhs = await s().run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw07_monoid_right_identity():
    """s ⊕ empty  ≡  s"""
    lhs = await s().concat(ComposableStream.empty()).run_to_list()
    rhs = await s().run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw08_monoid_associativity():
    """(a ⊕ b) ⊕ c  ≡  a ⊕ (b ⊕ c)"""
    # Fresh streams for each side — streams are single-use.
    def a() -> ComposableStream[int]:
        return ComposableStream.of(1, 2)

    def b() -> ComposableStream[int]:
        return ComposableStream.of(3, 4)

    def c() -> ComposableStream[int]:
        return ComposableStream.of(5, 6)

    lhs = await a().concat(b()).concat(c()).run_to_list()
    rhs = await a().concat(b().concat(c())).run_to_list()
    assert lhs == rhs


# =================================================================
#  Derivation equivalence — optimized operator vs categorical form
# =================================================================


@pytest.mark.asyncio
async def test_slw09_filter_derivation():
    """filter(p) ≡ flat_map(λx. of(x) if p(x) else empty())"""
    p = lambda x: x % 2 == 0

    lhs = await s().filter(p).run_to_list()
    rhs = await s().flat_map(
        lambda x: ComposableStream.of(x) if p(x) else ComposableStream.empty()
    ).run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw10_take_derivation():
    """take(n) ≡ zip_with(unfold(0, counter<n)).map(fst)"""
    lhs = await s().take(3).run_to_list()
    rhs = await (
        s()
        .zip_with(
            ComposableStream.unfold(0, lambda i: (i, i + 1) if i < 3 else None)
        )
        .map(lambda pair: pair[0])
        .run_to_list()
    )
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw11_drop_derivation():
    """drop(n) ≡ enumerate().flat_map(λ(i,v). of(v) if i>=n else empty())"""
    lhs = await s().drop(2).run_to_list()
    rhs = await (
        s()
        .enumerate()
        .flat_map(
            lambda t: ComposableStream.of(t[1])
            if t[0] >= 2
            else ComposableStream.empty()
        )
        .run_to_list()
    )
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw12_map_concat_derivation():
    """map_concat(f) ≡ flat_map(λx. from_iterable(f(x)))"""
    f = lambda x: [x] * x

    lhs = await s().map_concat(f).run_to_list()
    rhs = await s().flat_map(
        lambda x: ComposableStream.from_iterable(f(x))
    ).run_to_list()
    assert lhs == rhs


@pytest.mark.asyncio
async def test_slw13_intersperse_derivation():
    """intersperse(sep) ≡ enumerate().flat_map(λ(i,v). of(v) if i==0 else of(sep).concat(of(v)))"""
    lhs = await (
        ComposableStream.from_list(["a", "b", "c"]).intersperse(",").run_to_list()
    )
    rhs = await (
        ComposableStream.from_list(["a", "b", "c"])
        .enumerate()
        .flat_map(
            lambda t: ComposableStream.of(t[1])
            if t[0] == 0
            else ComposableStream.of(",").concat(ComposableStream.of(t[1]))
        )
        .run_to_list()
    )
    assert lhs == rhs
