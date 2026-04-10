import time
import sys
import os
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

def run_alice():
    # On détruit l'ancien si il existe pour repartir à zéro
    EasySHM("bidir_demo").destroy()
    
    shm = EasySHM("bidir_demo", size=1024)
    arr = shm.as_ndarray(shape=(2,), dtype='int32')
    
    print("[Alice] Prête. En attente que Bob se connecte...")
    
    # On attend que le compteur de séquence commence (Bob va le faire bouger)
    while shm.write_seq == 0:
        time.sleep(0.1)

    print("[Alice] Bob est arrivé ! début de l'échange.")
    
    for i in range(1, 4):
        val = i * 111
        print(f"[Alice] ===> Envoi de {val}")
        arr[0] = val
        shm.write(b"", offset=0) # Signal
        
        print("[Alice] ... Attente de Bob ...")
        if shm.wait_update(timeout=5.0):
            print(f"[Alice] <=== Reçu de Bob : {arr[1]}")
        else:
            print("[Alice] !!! Timeout en attendant Bob")
        
        time.sleep(1)

    print("[Alice] Terminé.")
    shm.close()

if __name__ == "__main__":
    run_alice()
