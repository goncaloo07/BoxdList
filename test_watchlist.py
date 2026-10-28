import pytest

import boxdlist
from boxdlist import (ano_como_int, carregar_filmes, escolher_aleatorio,
                       esta_incompleto, filtrar, guardar_ultimo_caminho, ler_ultimo_caminho,
                       normalizar, ordenar_filmes)


def filme(nome, ano="2000", data="2024-01-01", url="https://boxd.it/x"):
    return {"nome": nome, "ano": ano, "data": data, "url": url}


@pytest.fixture
def filmes():
    return [
        filme("Amélie", "2001", "2024-03-01"),
        filme("Blade Runner", "1982", "2024-01-15"),
        filme("Cidade de Deus", "2002", "2024-02-10"),
        filme("Sem Ano", "", "2024-04-01"),
    ]


def escrever_csv(caminho, linhas, cabecalho="Date,Name,Year,Letterboxd URI"):
    caminho.write_text(cabecalho + "\n" + "\n".join(linhas) + "\n",
                       encoding="utf-8")
    return str(caminho)


# ---------- normalizar ----------
def test_normalizar_remove_acentos_e_maiusculas():
    assert normalizar("Amélie") == "amelie"
    assert normalizar("AÇÃO") == "acao"


def test_normalizar_texto_vazio():
    assert normalizar("") == ""


# ---------- ano_como_int ----------
@pytest.mark.parametrize("valor, esperado", [
    ("1999", 1999), (" 2001 ", 2001), ("", None), ("abc", None), ("19a9", None),
])
def test_ano_como_int(valor, esperado):
    assert ano_como_int(valor) == esperado


# ---------- carregar_filmes ----------
def test_carregar_filmes_le_colunas(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", [
        "2024-01-01,Amélie,2001,https://boxd.it/a",
        "2024-01-02,Blade Runner,1982,https://boxd.it/b",
    ])
    resultado, ignoradas = carregar_filmes(caminho)
    assert ignoradas == 0
    assert resultado == [
        {"nome": "Amélie", "ano": "2001", "data": "2024-01-01",
         "url": "https://boxd.it/a"},
        {"nome": "Blade Runner", "ano": "1982", "data": "2024-01-02",
         "url": "https://boxd.it/b"},
    ]


def test_carregar_filmes_aceita_bom(tmp_path):
    caminho = tmp_path / "w.csv"
    caminho.write_text(
        "Date,Name,Year,Letterboxd URI\n2024-01-01,X,2000,u\n",
        encoding="utf-8-sig")
    assert carregar_filmes(str(caminho))[0][0]["nome"] == "X"


def test_carregar_filmes_linha_incompleta_nao_rebenta(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", ["2024-01-01,Só Nome"])
    resultado, ignoradas = carregar_filmes(caminho)
    assert ignoradas == 0
    assert resultado[0]["nome"] == "Só Nome"
    assert resultado[0]["ano"] == ""
    assert resultado[0]["url"] == ""


def test_carregar_filmes_colunas_em_falta(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", ["a,b"], cabecalho="Foo,Bar")
    with pytest.raises(ValueError):
        carregar_filmes(caminho)


def test_carregar_filmes_ficheiro_vazio(tmp_path):
    caminho = tmp_path / "vazio.csv"
    caminho.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        carregar_filmes(str(caminho))


def test_carregar_filmes_ficheiro_inexistente(tmp_path):
    with pytest.raises(OSError):
        carregar_filmes(str(tmp_path / "nao_existe.csv"))


def test_carregar_filmes_ignora_linha_sem_nome(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", [
        "2024-01-01,,2001,https://boxd.it/a",
        "2024-01-02,Blade Runner,1982,https://boxd.it/b",
    ])
    resultado, ignoradas = carregar_filmes(caminho)
    assert [f["nome"] for f in resultado] == ["Blade Runner"]
    assert ignoradas == 1


def test_carregar_filmes_linha_em_branco_nao_conta_como_erro(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", [",,,", "2024-01-02,X,1982,https://boxd.it/b"])
    resultado, ignoradas = carregar_filmes(caminho)
    assert len(resultado) == 1
    assert ignoradas == 0


def test_carregar_filmes_ignora_linha_com_colunas_a_mais(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", [
        "2024-01-01,Filme, com virgula,2001,https://boxd.it/a",
        "2024-01-02,Bom,1982,https://boxd.it/b",
    ])
    resultado, ignoradas = carregar_filmes(caminho)
    assert [f["nome"] for f in resultado] == ["Bom"]
    assert ignoradas == 1


def test_carregar_filmes_campos_invalidos_ficam_vazios(tmp_path):
    caminho = escrever_csv(tmp_path / "w.csv", [
        "ontem,Filme,abcd,link-estranho",
    ])
    resultado, _ = carregar_filmes(caminho)
    assert resultado == [{"nome": "Filme", "ano": "", "data": "", "url": ""}]


def test_esta_incompleto():
    assert not esta_incompleto(filme("A"))
    assert esta_incompleto(filme("A", ano=""))
    assert esta_incompleto(filme("A", data=""))
    assert esta_incompleto(filme("A", url=""))


# ---------- filtrar ----------
def test_filtrar_sem_criterios_devolve_tudo(filmes):
    assert filtrar(filmes, "", None, None) == filmes


def test_filtrar_por_nome_ignora_acentos_e_maiusculas(filmes):
    resultado = filtrar(filmes, "AMELIE", None, None)
    assert [f["nome"] for f in resultado] == ["Amélie"]


def test_filtrar_por_nome_ignora_espacos_nas_pontas(filmes):
    assert len(filtrar(filmes, "  blade  ", None, None)) == 1


def test_filtrar_por_intervalo_de_anos(filmes):
    resultado = filtrar(filmes, "", 2000, 2002)
    assert [f["nome"] for f in resultado] == ["Amélie", "Cidade de Deus"]


def test_filtrar_so_ano_minimo(filmes):
    resultado = filtrar(filmes, "", 2002, None)
    assert [f["nome"] for f in resultado] == ["Cidade de Deus"]


def test_filtrar_so_ano_maximo(filmes):
    resultado = filtrar(filmes, "", None, 1990)
    assert [f["nome"] for f in resultado] == ["Blade Runner"]


def test_filtrar_com_intervalo_exclui_filmes_sem_ano(filmes):
    resultado = filtrar(filmes, "", 1900, 2100)
    assert "Sem Ano" not in [f["nome"] for f in resultado]


def test_filtrar_sem_intervalo_mantem_filmes_sem_ano(filmes):
    assert "Sem Ano" in [f["nome"] for f in filtrar(filmes, "", None, None)]


def test_filtrar_combina_nome_e_ano(filmes):
    assert filtrar(filmes, "c", 2002, 2002) == [filmes[2]]


def test_filtrar_sem_resultados(filmes):
    assert filtrar(filmes, "zzz", None, None) == []


# ---------- ordenar_filmes ----------
def nomes(lista):
    return [f["nome"] for f in lista]


def test_ordenar_sem_coluna_devolve_copia_na_mesma_ordem(filmes):
    resultado = ordenar_filmes(filmes, None, False)
    assert resultado == filmes
    assert resultado is not filmes


def test_ordenar_nao_altera_lista_original(filmes):
    original = list(filmes)
    ordenar_filmes(filmes, "ano", True)
    assert filmes == original


def test_ordenar_por_nome(filmes):
    assert nomes(ordenar_filmes(filmes, "nome", False)) == [
        "Amélie", "Blade Runner", "Cidade de Deus", "Sem Ano"]


def test_ordenar_por_nome_inversa(filmes):
    assert nomes(ordenar_filmes(filmes, "nome", True))[0] == "Sem Ano"


def test_ordenar_por_ano_e_numerica_com_vazios_primeiro(filmes):
    assert nomes(ordenar_filmes(filmes, "ano", False)) == [
        "Sem Ano", "Blade Runner", "Amélie", "Cidade de Deus"]


def test_ordenar_ano_nao_e_lexicografico():
    lista = [filme("A", "999"), filme("B", "1000")]
    assert nomes(ordenar_filmes(lista, "ano", False)) == ["A", "B"]


def test_ordenar_por_data(filmes):
    assert nomes(ordenar_filmes(filmes, "data", False)) == [
        "Blade Runner", "Cidade de Deus", "Amélie", "Sem Ano"]


# ---------- escolher_aleatorio ----------
def test_aleatorio_nunca_repete_o_ultimo(filmes):
    ultimo = filmes[0]
    for _ in range(200):
        assert escolher_aleatorio(filmes, ultimo) is not ultimo


def test_aleatorio_com_um_so_filme_devolve_esse(filmes):
    unico = [filmes[0]]
    assert escolher_aleatorio(unico, filmes[0]) is filmes[0]


def test_aleatorio_sem_ultimo(filmes):
    assert escolher_aleatorio(filmes, None) in filmes


def test_aleatorio_usa_random_choice(filmes, monkeypatch):
    monkeypatch.setattr(boxdlist.random, "choice", lambda seq: seq[-1])
    assert escolher_aleatorio(filmes, None) is filmes[-1]


# ---------- configuração ----------
def test_config_guardar_e_ler(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "FICHEIRO_CONFIG", tmp_path / "cfg.json")
    guardar_ultimo_caminho("/tmp/w.csv")
    assert ler_ultimo_caminho() == "/tmp/w.csv"


def test_config_inexistente_devolve_none(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "FICHEIRO_CONFIG", tmp_path / "nao.json")
    assert ler_ultimo_caminho() is None


def test_config_corrompida_devolve_none(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{isto não é json", encoding="utf-8")
    monkeypatch.setattr(boxdlist, "FICHEIRO_CONFIG", cfg)
    assert ler_ultimo_caminho() is None


def test_config_guardar_em_pasta_inexistente_nao_rebenta(tmp_path, monkeypatch):
    monkeypatch.setattr(boxdlist, "FICHEIRO_CONFIG",
                        tmp_path / "pasta_que_nao_existe" / "cfg.json")
    guardar_ultimo_caminho("/tmp/w.csv")   # não deve levantar erro