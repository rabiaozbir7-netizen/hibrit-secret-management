from fastapi import FastAPI, HTTPException
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import hvac
import psycopg2
import os

app = FastAPI(title="Hibrit Secret Management (HashiCorp Vault + Azure Key Vault)")

# --- 1. HASHICORP VAULT AYARLARI (BİRİNCİL KAYNAK) ---
HVAC_URL = "http://127.0.0.1:8200"
HVAC_TOKEN = os.getenv("VAULT_TOKEN", "root-token-demo")
HVAC_MOUNT_POINT = "secret"
HVAC_SECRET_PATH = "veritabani"
HVAC_SECRET_KEY = "DB_PASSWORD"

# --- 2. AZURE KEY VAULT AYARLARI (YEDEK / FALLBACK KAYNAK) ---
AZURE_VAULT_URL = "https://kv-rabia-proje25.vault.azure.net/"
azure_credential = DefaultAzureCredential()
azure_client = SecretClient(vault_url=AZURE_VAULT_URL, credential=azure_credential)

# --- 3. POSTGRESQL AYARLARI ---
DB_HOST = "127.0.0.1"
DB_PORT = "5432"
DB_NAME = "sirket_db"
DB_USER = "admin"


def get_hybrid_db_password() -> dict:
    """
    Önce HashiCorp Vault'tan parolayı çekmeyi dener.
    Erişilemezse veya hata alırsa otomatik olarak Azure Key Vault'a (Fallback) geçer.
    """
    # AŞAMA 1: Birincil Kaynak (HashiCorp Vault)
    try:
        hvac_client = hvac.Client(url=HVAC_URL, token=HVAC_TOKEN, timeout=2)
        if not hvac_client.is_authenticated():
            raise Exception("HashiCorp Vault kimlik doğrulaması başarısız veya Vault mühürlü (sealed).")

        secret_response = hvac_client.secrets.kv.v2.read_secret_version(
            path=HVAC_SECRET_PATH,
            mount_point=HVAC_MOUNT_POINT
        )
        password = secret_response["data"]["data"][HVAC_SECRET_KEY]

        return {
            "password": password,
            "aktif_kaynak": "HashiCorp Vault (Primary - Self-Hosted)",
            "failover_tetiklendi_mi": False,
            "hata_detayi": None
        }

    except Exception as hvac_err:
        # AŞAMA 2: İkincil Kaynak (Azure Key Vault Fallback)
        try:
            azure_secret = azure_client.get_secret("DB-PASSWORD")
            return {
                "password": azure_secret.value,
                "aktif_kaynak": "Azure Key Vault (Secondary - Cloud Fallback)",
                "failover_tetiklendi_mi": True,
                "hata_detayi": f"Primary Vault hatası nedeniyle buluta geçildi: {str(hvac_err)}"
            }
        except Exception as azure_err:
            raise Exception(
                f"Kritik Hata: Her iki gizli anahtar kasasına da ulaşılamadı! "
                f"[HashiCorp: {hvac_err}] | [Azure: {azure_err}]"
            )


@app.get("/")
def durum_ozeti():
    return {
        "mimari": "Hibrit Multi-Cloud / On-Prem Secret Management",
        "primary": f"HashiCorp Vault ({HVAC_URL})",
        "fallback": f"Azure Key Vault ({AZURE_VAULT_URL})"
    }


@app.get("/hibrit-db-test")
def hibrit_veritabani_baglantisi():
    try:
        # 1. Hibrit yöneticiden parolayı ve hangi kaynaktan geldiğini al
        secret_info = get_hybrid_db_password()

        # 2. Çekilen parola ile PostgreSQL'e bağlan
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=secret_info["password"]
        )
        cursor = conn.cursor()
        cursor.execute("SELECT version(), current_user, now();")
        db_version, current_user, server_time = cursor.fetchone()
        cursor.close()
        conn.close()

        return {
            "baglanti_durumu": "Başarılı",
            "aktif_secret_kaynagi": secret_info["aktif_kaynak"],
            "failover_durumu": secret_info["failover_tetiklendi_mi"],
            "bilgi_notu": secret_info["hata_detayi"],
            "veritabani_kullanicisi": current_user,
            "sunucu_zamani": str(server_time),
            "postgresql_versiyonu": db_version
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))