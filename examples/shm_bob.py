import time
import sys
import os
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

def run_bob():
    print("[Bob] Connexion...")
    shm = EasySHM("bidir_demo")
    arr = shm.as_ndarray(shape=(2,), dtype='int32')
    
    # On signale notre présence en faisant un write à vide
    print("[Bob] Signalement de présence à Alice...")
    shm.write(b"", offset=0)
    
    print("[Bob] Prêt. J'écris à l'index 1 et je lis à l'index 0.")
    
    for i in range(1, 4):
        print("[Bob] ... Attente d'Alice ...")
        if shm.wait_update(timeout=5.0):
            print(f"[Bob] <=== Reçu d'Alice : {arr[0]}")
            
            val = i * 999
            print(f"[Bob] ===> Envoi de {val}")
            arr[1] = val
            shm.write(b"", offset=0) # Signal
        else:
            print("[Bob] !!! Timeout en attendant Alice")
            
        time.sleep(1)

    print("[Bob] Terminé.")
    shm.close()

if __name__ == "__main__":
    run_bob()
