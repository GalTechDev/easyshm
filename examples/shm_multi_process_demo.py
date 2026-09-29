import multiprocessing
import time
import sys
import os
import numpy as np

# Ajouter le chemin pour trouver easyshm
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from easyshm import EasySHM

def writer_process():
    print("[Writer] Démarrage...")
    # On crée le segment
    shm = EasySHM("demo_shared", size=1024)
    
    # On crée un tableau numpy mappé sur la SHM
    arr = shm.as_view("numpy", shape=(5,), dtype='int32')
    time.sleep(0.5)  # Laisse le lecteur se connecter avant la première écriture

    for i in range(5):
        val = (i + 1) * 10
        print(f"[Writer] Écriture de {val} à l'index {i}...")
        arr[i] = val
        # On signale le changement
        shm.write(b"", offset=0) # Déclenche le write_seq et le signal
        time.sleep(1)
    
    print("[Writer] Terminé.")
    shm.close()

def reader_process():
    print("[Reader] Attente de données...")
    shm = EasySHM("demo_shared")
    
    # On mappe le même segment
    arr = shm.as_view("numpy", shape=(5,), dtype='int32')
    
    # On s'arrête quand la dernière valeur est arrivée (un réveil peut regrouper
    # plusieurs écritures, on ne compte donc pas les signaux)
    deadline = time.time() + 15
    while arr[4] != 50 and time.time() < deadline:
        # On attend le signal noyau (bloquant, 0% CPU)
        if shm.wait_update(timeout=2.0):
            print(f"[Reader] Signal reçu ! Contenu actuel : {arr}")

    print("[Reader] Terminé." if arr[4] == 50 else "[Reader] Abandon : données incomplètes.")
    shm.close()

if __name__ == "__main__":
    # Nettoyage préalable si nécessaire
    temp_shm = EasySHM("demo_shared")
    temp_shm.destroy()

    print("--- DÉMONSTRATION SHARED MEMORY ---")
    p1 = multiprocessing.Process(target=writer_process)
    p2 = multiprocessing.Process(target=reader_process)

    p1.start()
    p2.start()

    p1.join()
    p2.join()
    print("--- FIN DE LA DÉMO ---")
