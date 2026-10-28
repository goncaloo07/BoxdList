# ---------------------------------------------------------------
# Aplicação de Watchlist (Letterboxd)
# Importa o CSV exportado do Letterboxd, mostra os filmes numa tabela
# (com pesquisa, filtros e ordenação) e sorteia um filme à sorte.
# ---------------------------------------------------------------

import csv
import json
import random
import re
import tkinter as tk
import unicodedata
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ---------- Constantes ----------
# (id interno, título do cabeçalho, largura em pixels)
COLUNAS = (("nome", "Filme", 440),
           ("ano", "Ano", 70),
           ("data", "Adicionado", 110))
OBRIGATORIAS = {"Date", "Name", "Year", "Letterboxd URI"}
FICHEIRO_CONFIG = Path.home() / ".watchlist_config.json"
ATRASO_PESQUISA_MS = 200   # espera depois da última tecla antes de filtrar
SEM_FILTRO = "Todas"
VAZIO = "—"             # mostrado na tabela quando falta um dado
ANO_VALIDO = re.compile(r"\d{4}")
DATA_VALIDA = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------- Lógica (funções puras, fáceis de testar) ----------
def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos: 'Amélie' -> 'amelie'."""
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def carregar_filmes(caminho: str) -> tuple[list[dict], int]:
    """Lê o CSV e devolve (filmes, linhas_ignoradas).

    Ignora linhas sem nome ou com colunas a mais (não dá para saber a que
    campo pertence cada valor). Anos, datas e links inválidos ficam vazios.
    """
    filmes = []
    ignoradas = 0
    # utf-8-sig remove o BOM; newline="" é a forma recomendada para CSVs
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        leitor = csv.DictReader(f)

        if not leitor.fieldnames or not OBRIGATORIAS.issubset(leitor.fieldnames):
            raise ValueError(
                "O ficheiro não parece ser uma watchlist do Letterboxd.\n"
                "Colunas esperadas: Date, Name, Year, Letterboxd URI")

        for linha in leitor:
            # Colunas a mais: o csv guarda-as sob a chave None
            if None in linha:
                ignoradas += 1
                continue

            nome = (linha.get("Name") or "").strip()
            if not nome:
                # linhas totalmente em branco não contam como erro
                if any((v or "").strip() for v in linha.values()):
                    ignoradas += 1
                continue

            ano = (linha.get("Year") or "").strip()
            data = (linha.get("Date") or "").strip()
            url = (linha.get("Letterboxd URI") or "").strip()
            filmes.append({
                "nome": nome,
                "ano": ano if ANO_VALIDO.fullmatch(ano) else "",
                "data": data if DATA_VALIDA.fullmatch(data) else "",
                "url": url if url.startswith("http") else "",
            })
    return filmes, ignoradas


def esta_incompleto(filme: dict) -> bool:
    """True se faltar o ano, a data ou o link."""
    return not (filme["ano"] and filme["data"] and filme["url"])


def ano_como_int(valor: str) -> int | None:
    """Converte um ano em texto para int; devolve None se não for válido."""
    valor = valor.strip()
    return int(valor) if valor.isdigit() else None


def filtrar(filmes: list[dict], termo: str,
            ano_min: int | None, ano_max: int | None) -> list[dict]:
    """Filtra por nome (sem acentos/maiúsculas) e por intervalo de anos."""
    termo = normalizar(termo.strip())
    resultado = []
    for f in filmes:
        if termo and termo not in normalizar(f["nome"]):
            continue
        if ano_min is not None or ano_max is not None:
            ano = ano_como_int(f["ano"])
            if ano is None:
                continue
            if ano_min is not None and ano < ano_min:
                continue
            if ano_max is not None and ano > ano_max:
                continue
        resultado.append(f)
    return resultado


def ordenar_filmes(filmes: list[dict], coluna: str | None,
                   inversa: bool) -> list[dict]:
    """Devolve uma cópia ordenada; a lista original não é alterada."""
    if coluna is None:
        return list(filmes)

    def chave(f: dict):
        valor = f[coluna]
        if coluna == "ano":
            return ano_como_int(valor) or 0   # anos vazios ficam no início
        if coluna == "nome":
            return normalizar(valor)
        return valor.lower()                  # datas AAAA-MM-DD ordenam como texto

    return sorted(filmes, key=chave, reverse=inversa)


def escolher_aleatorio(filmes: list[dict], ultimo: dict | None) -> dict:
    """Escolhe um filme à sorte, evitando repetir o último (se possível)."""
    candidatos = [f for f in filmes if f is not ultimo]
    return random.choice(candidatos or filmes)


# ---------- Configuração (lembrar o último CSV) ----------
def ler_ultimo_caminho() -> str | None:
    try:
        dados = json.loads(FICHEIRO_CONFIG.read_text(encoding="utf-8"))
        return dados.get("ultimo_csv")
    except (OSError, ValueError):
        return None


def guardar_ultimo_caminho(caminho: str) -> None:
    try:
        FICHEIRO_CONFIG.write_text(
            json.dumps({"ultimo_csv": caminho}), encoding="utf-8")
    except OSError:
        pass   # não é crítico: a app funciona na mesma


# ---------- Interface ----------
class App(tk.Tk):
    """Janela principal. Herda de tk.Tk, ou seja, a própria classe é a janela."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Watchlist")
        self.geometry("760x600")

        self.filmes: list[dict] = []     # lista original, na ordem do CSV
        self.visiveis: list[dict] = []   # filtrados e ordenados (o que se vê)
        self.coluna_ordem: str | None = None
        self.ordem_inversa = False
        self.ultimo_sorteado: dict | None = None
        self._job_pesquisa = None        # id do after() pendente (debounce)

        # ---------- Linha 1: importar, pesquisa e contador ----------
        topo = ttk.Frame(self, padding=8)
        topo.pack(fill="x")

        ttk.Button(topo, text="Importar CSV (Ctrl+O)",
                   command=self.importar_csv).pack(side="left", padx=(0, 10))

        ttk.Label(topo, text="Pesquisar:").pack(side="left")
        self.pesquisa = tk.StringVar()
        self.pesquisa.trace_add("write", lambda *_: self.agendar_atualizacao())
        ttk.Entry(topo, textvariable=self.pesquisa).pack(
            side="left", fill="x", expand=True, padx=6)

        self.contador = ttk.Label(topo, text="")
        self.contador.pack(side="right")

        # ---------- Linha 2: filtros por ano e por década ----------
        filtros = ttk.Frame(self, padding=(8, 0, 8, 6))
        filtros.pack(fill="x")

        ttk.Label(filtros, text="Anos de:").pack(side="left")
        self.ano_min = tk.StringVar()
        ttk.Entry(filtros, textvariable=self.ano_min, width=6).pack(
            side="left", padx=4)
        ttk.Label(filtros, text="a").pack(side="left")
        self.ano_max = tk.StringVar()
        ttk.Entry(filtros, textvariable=self.ano_max, width=6).pack(
            side="left", padx=4)
        for var in (self.ano_min, self.ano_max):
            var.trace_add("write", lambda *_: self.agendar_atualizacao())

        ttk.Label(filtros, text="Década:").pack(side="left", padx=(14, 4))
        self.decada = ttk.Combobox(filtros, state="readonly", width=8,
                                   values=[SEM_FILTRO])
        self.decada.set(SEM_FILTRO)
        self.decada.pack(side="left")
        self.decada.bind("<<ComboboxSelected>>", lambda _: self.escolher_decada())

        ttk.Button(filtros, text="Limpar filtros",
                   command=self.limpar_filtros).pack(side="left", padx=14)

        # ---------- Tabela ----------
        meio = ttk.Frame(self, padding=(8, 0))
        meio.pack(fill="both", expand=True)

        self.tabela = ttk.Treeview(
            meio, columns=[c[0] for c in COLUNAS], show="headings")
        for col, titulo, larg in COLUNAS:
            # c=col fixa o valor de col em cada volta do ciclo
            self.tabela.heading(col, text=titulo,
                                command=lambda c=col: self.ordenar(c))
            self.tabela.column(col, width=larg, anchor="w")

        barra = ttk.Scrollbar(meio, orient="vertical",
                              command=self.tabela.yview)
        self.tabela.configure(yscrollcommand=barra.set)
        self.tabela.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        # Mensagem por cima da tabela quando não há nada para mostrar
        self.aviso = ttk.Label(self.tabela, text="", foreground="gray")

        self.tabela.bind("<Double-1>", lambda _: self.abrir_link())

        # ---------- Zona de baixo ----------
        baixo = ttk.Frame(self, padding=8)
        baixo.pack(fill="x")

        ttk.Button(baixo, text="Filme aleatório",
                   command=self.aleatorio).pack(side="left")
        ttk.Button(baixo, text="Abrir no Letterboxd (Enter)",
                   command=self.abrir_link).pack(side="left", padx=6)

        self.resultado = ttk.Label(
            baixo, text="", font=("TkDefaultFont", 11, "bold"))
        self.resultado.pack(side="left", padx=12)

        # ---------- Atalhos de teclado ----------
        self.bind("<Control-o>", lambda _: self.importar_csv())
        self.bind("<Return>", lambda _: self.abrir_link())

        self.atualizar_lista()
        self.carregar_ultimo_csv()

    # ----- Carregar dados -----
    def carregar_ultimo_csv(self) -> None:
        """Ao arrancar, tenta voltar a abrir o último CSV usado."""
        caminho = ler_ultimo_caminho()
        if caminho and Path(caminho).is_file():
            try:
                self.definir_filmes(carregar_filmes(caminho)[0])
            except (OSError, ValueError, UnicodeDecodeError, csv.Error):
                pass   # ficheiro mudou ou ficou inválido: arranca vazio

    def importar_csv(self) -> None:
        caminho = filedialog.askopenfilename(
            title="Escolher CSV da watchlist",
            filetypes=[("CSV", "*.csv"), ("Todos os ficheiros", "*.*")])
        if not caminho:
            return

        try:
            filmes, ignoradas = carregar_filmes(caminho)
        except (OSError, ValueError, UnicodeDecodeError, csv.Error) as erro:
            messagebox.showerror("Erro ao importar", str(erro))
            return

        guardar_ultimo_caminho(caminho)
        self.definir_filmes(filmes)

        incompletos = sum(esta_incompleto(f) for f in filmes)
        if ignoradas or incompletos:
            messagebox.showwarning(
                "Importação",
                f"{len(filmes)} filmes importados.\n"
                f"- {ignoradas} linhas ignoradas (sem nome ou mal formatadas)\n"
                f"- {incompletos} filmes com dados em falta (mostrados com {VAZIO})")

    def definir_filmes(self, filmes: list[dict]) -> None:
        """Substitui a watchlist atual e refresca tudo."""
        self.filmes = filmes
        self.ultimo_sorteado = None
        self.resultado.config(text="")

        # Atualiza as décadas disponíveis no filtro
        anos = [a for a in (ano_como_int(f["ano"]) for f in filmes) if a]
        decadas = sorted({a // 10 * 10 for a in anos})
        self.decada.config(values=[SEM_FILTRO] + [f"{d}s" for d in decadas])
        self.limpar_filtros()

    # ----- Filtros -----
    def escolher_decada(self) -> None:
        escolha = self.decada.get()
        if escolha == SEM_FILTRO:
            self.ano_min.set("")
            self.ano_max.set("")
        else:
            inicio = int(escolha[:-1])   # "1990s" -> 1990
            self.ano_min.set(str(inicio))
            self.ano_max.set(str(inicio + 9))

    def limpar_filtros(self) -> None:
        # Cada set() dispara o agendamento; só corre uma atualização no fim
        self.pesquisa.set("")
        self.ano_min.set("")
        self.ano_max.set("")
        self.decada.set(SEM_FILTRO)
        self.atualizar_lista()

    # ----- Atualização da tabela -----
    def agendar_atualizacao(self) -> None:
        """Espera um pouco após a última alteração (evita filtrar a cada tecla)."""
        if self._job_pesquisa is not None:
            self.after_cancel(self._job_pesquisa)
        self._job_pesquisa = self.after(ATRASO_PESQUISA_MS, self.atualizar_lista)

    def atualizar_lista(self) -> None:
        """Filtra, ordena (sem tocar na lista original) e refaz a tabela."""
        if self._job_pesquisa is not None:
            self.after_cancel(self._job_pesquisa)
            self._job_pesquisa = None

        filtrados = filtrar(self.filmes, self.pesquisa.get(),
                            ano_como_int(self.ano_min.get()),
                            ano_como_int(self.ano_max.get()))
        self.visiveis = ordenar_filmes(
            filtrados, self.coluna_ordem, self.ordem_inversa)

        self.tabela.delete(*self.tabela.get_children())
        # iid = índice em self.visiveis, para recuperar o filme selecionado
        for i, f in enumerate(self.visiveis):
            self.tabela.insert("", "end", iid=str(i),
                               values=(f["nome"], f["ano"] or VAZIO, f["data"] or VAZIO))

        self.contador.config(text=f"{len(self.visiveis)} / {len(self.filmes)}")
        self.atualizar_cabecalhos()
        self.atualizar_aviso()

    def atualizar_cabecalhos(self) -> None:
        """Mostra ▲ / ▼ na coluna pela qual está ordenado."""
        seta = " ▼" if self.ordem_inversa else " ▲"
        for col, titulo, _ in COLUNAS:
            texto = titulo + (seta if col == self.coluna_ordem else "")
            self.tabela.heading(col, text=texto)

    def atualizar_aviso(self) -> None:
        if not self.filmes:
            self.aviso.config(text="Importa um CSV para começar (Ctrl+O)")
        elif not self.visiveis:
            self.aviso.config(text="Nenhum filme corresponde aos filtros")
        else:
            self.aviso.place_forget()
            return
        self.aviso.place(relx=0.5, rely=0.4, anchor="center")

    # ----- Ações -----
    def ordenar(self, coluna: str) -> None:
        """Ordena pela coluna; clicar na mesma coluna inverte a direção."""
        if coluna == self.coluna_ordem:
            self.ordem_inversa = not self.ordem_inversa
        else:
            self.coluna_ordem = coluna
            self.ordem_inversa = False
        self.atualizar_lista()

    def aleatorio(self) -> None:
        if not self.visiveis:
            messagebox.showinfo("Watchlist",
                                "Nenhum filme para escolher. Importa um CSV primeiro.")
            return

        f = escolher_aleatorio(self.visiveis, self.ultimo_sorteado)
        self.ultimo_sorteado = f
        iid = str(self.visiveis.index(f))

        self.tabela.selection_set(iid)
        self.tabela.see(iid)
        ano = f" ({f['ano']})" if f["ano"] else ""
        self.resultado.config(text=f["nome"] + ano)

    def abrir_link(self) -> None:
        sel = self.tabela.selection()
        if not sel:
            return
        url = self.visiveis[int(sel[0])]["url"]
        if url:
            webbrowser.open(url)
        else:
            messagebox.showinfo("Watchlist", "Este filme não tem link.")


if __name__ == "__main__":
    App().mainloop()