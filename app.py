import os
import time
import shutil
import uuid
from flask import Flask, request, send_file, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# ==========================================================
# FUNÇÕES DE APOIO
# ==========================================================

def criar_driver(caminho_download):
    """Configura o Chrome em modo headless para o Docker"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    prefs = {
        "download.default_directory": caminho_download,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "profile.default_content_settings.popups": 0,
        "plugins.always_open_pdf_externally": True,
        "profile.default_content_setting_values.automatic_downloads": 1
    }
    options.add_experimental_option("prefs", prefs)
    
    # Aponta para o ChromeDriver no container Linux
    service = Service("/usr/bin/chromedriver")
    return webdriver.Chrome(service=service, options=options)


def esperar_download(caminho_download, arquivos_antes, timeout=60):
    """Aguarda até que o arquivo termine de ser baixado"""
    tempo_inicial = time.time()
    while True:
        arquivos_agora = set(os.listdir(caminho_download))
        arquivos_novos = arquivos_agora - arquivos_antes

        baixando = [
            arq for arq in arquivos_agora
            if arq.endswith(".crdownload") or arq.endswith(".tmp")
        ]

        if arquivos_novos and not baixando:
            return True

        if time.time() - tempo_inicial > timeout:
            print("⏰ Timeout: O download demorou demais.")
            return False

        time.sleep(1)


def baixaraquivos(driver, caminho_download):
    """Lógica original de cliques no Google Drive mantida"""
    all_windows = driver.window_handles
    for window in all_windows:
        driver.switch_to.window(window)
        if "https://drive.google.com/drive/folders/" in driver.current_url:
            break
    time.sleep(1)

    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((By.CLASS_NAME, "FAGDGb"))
    )
    time.sleep(2)

    elementos = driver.find_elements(By.CLASS_NAME, "FAGDGb")
    for elemento in elementos:
        elemento.click()
        time.sleep(2)

        try:
            arquivos_antes = set(os.listdir(caminho_download))
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div/div[5]/div[1]/div/div/div/div[2]/div/div[4]/div/div[2]/div/div[5]"))
            ).click()
            esperar_download(caminho_download, arquivos_antes)
        except Exception:
            elemento.click()
            time.sleep(2)
            arquivos_antes = set(os.listdir(caminho_download))
            WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div/div[5]/div[1]/div/div/div/div[2]/div/div[4]/div/div[2]/div/div[5]"))
            ).click()
            esperar_download(caminho_download, arquivos_antes)

    return True


def salva_apenas_guia(pasta_origem, ficha):
    """Filtra o PDF da guia e apaga o resto"""
    ficha_limpa = str(ficha).replace(".0", "").strip()
    achou_guia = False
    caminho_guia_encontrada = ""

    for arquivo in os.listdir(pasta_origem):
        caminho_completo = os.path.join(pasta_origem, arquivo)

        if os.path.isfile(caminho_completo):
            nome_minusculo = arquivo.lower()

            if nome_minusculo.endswith(".pdf") and "guia" in nome_minusculo and not achou_guia:
                caminho_guia_encontrada = caminho_completo
                achou_guia = True
            else:
                try:
                    os.remove(caminho_completo)
                except Exception:
                    pass

    if not achou_guia:
        return None

    # Renomeia o arquivo dentro da mesma pasta temporária
    nome_novo_pdf = f"Guia - {ficha_limpa}.pdf"
    caminho_arquivo_final = os.path.join(pasta_origem, nome_novo_pdf)
    shutil.move(caminho_guia_encontrada, caminho_arquivo_final)
    
    return caminho_arquivo_final


# ==========================================================
# ROTA DA API (WEBHOOK QUE O N8N VAI CHAMAR)
# ==========================================================

@app.route('/processar-linha', methods=['POST'])
def processar_linha():
    # 1. Recebe os dados do n8n
    dados = request.get_json()
    if not dados:
        return jsonify({"erro": "Nenhum dado recebido"}), 400

    link = dados.get('link')
    ficha = dados.get('ficha')

    if not link or not ficha:
        return jsonify({"erro": "Link ou ficha ausentes"}), 400

    # Tratamento da URL para resolver o erro "invalid argument" do Selenium
    link = str(link).strip()
    if not link.startswith(('http://', 'https://')):
        link = f"https://{link}"

    print(f"🚀 Iniciando processamento da Ficha: {ficha} | Link: {link}")

    # 2. Cria uma pasta temporária ÚNICA para essa execução
    pasta_tmp = f"/tmp/downloads_{uuid.uuid4().hex}"
    os.makedirs(pasta_tmp, exist_ok=True)

    driver = None
    try:
        # 3. Inicia o Selenium e navega
        driver = criar_driver(pasta_tmp)
        driver.get(link)
        time.sleep(5)

        # 4. Executa a sua automação de clique/download
        baixaraquivos(driver, pasta_tmp)

        # 5. Processa os arquivos baixados e pega o caminho do arquivo final
        caminho_final = salva_apenas_guia(pasta_tmp, ficha)

        # 6. Devolve o arquivo via Webhook pro n8n
        if caminho_final and os.path.exists(caminho_final):
            nome_arquivo = os.path.basename(caminho_final)
            return send_file(
                caminho_final,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=nome_arquivo
            )
        else:
            return jsonify({"erro": f"Guia não encontrada para a ficha {ficha}"}), 404

    except Exception as e:
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

    finally:
        # 7. Limpa a memória e apaga a pasta temporária
        if driver:
            driver.quit()
        if os.path.exists(pasta_tmp):
            shutil.rmtree(pasta_tmp, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
