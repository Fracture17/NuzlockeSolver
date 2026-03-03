import pickle

with open("matchup_info.pkl", "rb") as f:
    m = pickle.load(f)
    print(m)
