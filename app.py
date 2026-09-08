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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

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
    """Nova lógica robusta: Botão Direito -> Download"""
    print("Aguardando carregamento da pasta do Drive...")
    time.sleep(5)  # Dá tempo para os arquivos renderizarem na tela
    
    try:
        # Encontra arquivos independentemente de estarem em 'grade' ou 'lista'
        arquivos = WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[role='row'], [role='gridcell']"))
        )
        
        print(f"Encontrados {len(arquivos)} arquivos/pastas. Iniciando extração...")
        
        for arquivo in arquivos:
            try:
                # Rola até o arquivo para garantir o clique
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", arquivo)
                time.sleep(1)
                
                # Clica com o botão direito no arquivo
                ActionChains(driver).context_click(arquivo).perform()
                time.sleep(2)
                
                # Pega as opções do menu que abriu
                opcoes_menu = driver.find_elements(By.CSS_SELECTOR, "div[role='menuitem']")
                
                clicou_download = False
                for opcao in opcoes_menu:
                    texto = opcao.text.lower()
                    if "download" in texto:
                        arquivos_antes = set(os.listdir(caminho_download))
                        opcao.click()
                        clicou_download = True
                        
                        # Verifica se o Google mostrou aquela tela de "Arquivo grande, não pode verificar vírus"
                        time.sleep(2)
                        try:
                            btn_virus = driver.find_element(By.XPATH, "//*[contains(translate(text(), 'DOWNLOAD', 'download'), 'download')]")
                            if btn_virus:
                                btn_virus.click()
                        except:
                            pass
                            
                        # Aguarda o download concluir
                        esperar_download(caminho_download, arquivos_antes)
                        break
                
                # Se a opção de download não existir ali (ex: clicou no fundo sem querer), fecha o menu
                if not clicou_download:
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    time.sleep(1)
                    
            except Exception as e:
                print(f"Ignorando elemento inválido. Erro: {e}")
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(1)
                
        return True
        
    except Exception as e:
        print(f"Erro principal no baixaraquivos: {e}")
        raise Exception("Nenhum arquivo encontrado ou a pasta demorou muito para carregar.")


def salva_apenas_guia(pasta_origem, ficha):
    """Filtra o PDF da guia e apaga o resto"""
    ficha_limpa = str(ficha).replace(".0", "").strip()
    achou_guia = False
    caminho_guia_encontrada = ""

    for arquivo in os.listdir(pasta_origem):
        caminho_completo = os.path.join(pasta_origem, arquivo)

        if os.path.isfile(caminho_completo):
            nome_minusculo = arquivo.lower()

            # Mantém apenas se for PDF e tiver "guia" no nome
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

    # Renomeia o arquivo que sobrou
    nome_novo_pdf = f"Guia - {ficha_limpa}.pdf"
    caminho_arquivo_final = os.path.join(pasta_origem, nome_novo_pdf)
    shutil.move(caminho_guia_encontrada, caminho_arquivo_final)
    
    return caminho_arquivo_final


# ==========================================================
# ROTA DA API (WEBHOOK QUE O N8N VAI CHAMAR)
# ==========================================================

@app.route('/processar-linha', methods=['POST'])
def processar_linha():
    dados = request.get_json()
    if not dados:
        return jsonify({"erro": "Nenhum dado recebido"}), 400

    link = dados.get('link')
    ficha = dados.get('ficha')

    if not link or not ficha:
        return jsonify({"erro": "Link ou ficha ausentes"}), 400

    # Tratamento da URL 
    link = str(link).strip()
    if not link.startswith(('http://', 'https://')):
        link = f"https://{link}"

    print(f"🚀 Iniciando processamento da Ficha: {ficha} | Link: {link}")

    pasta_tmp = f"/tmp/downloads_{uuid.uuid4().hex}"
    os.makedirs(pasta_tmp, exist_ok=True)

    driver = None
    try:
        driver = criar_driver(pasta_tmp)
        driver.get(link)
        
        # Chama a nova função de cliques robustos
        baixaraquivos(driver, pasta_tmp)

        # Filtra para devolver apenas a Guia PDF
        caminho_final = salva_apenas_guia(pasta_tmp, ficha)

        if caminho_final and os.path.exists(caminho_final):
            nome_arquivo = os.path.basename(caminho_final)
            return send_file(
                caminho_final,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=nome_arquivo
            )
        else:
            return jsonify({"erro": f"A pasta foi baixada, mas nenhum PDF com 'guia' no nome foi encontrado."}), 404

    except Exception as e:
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

    finally:
        if driver:
            driver.quit()
        if os.path.exists(pasta_tmp):
            shutil.rmtree(pasta_tmp, ignore_errors=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
